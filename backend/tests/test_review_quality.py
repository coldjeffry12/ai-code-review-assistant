import asyncio
import json

from app.schemas import BugFinding, ReviewRequest, ReviewResponse
from app.services.reviewer import (
    _ai_local_findings_prompt,
    _ai_language_detection_prompt,
    _ai_user_prompt,
    _bug_category,
    _code_sample_for_language_detection,
    _clear_review_cache,
    _detected_review_language,
    _fallback_review,
    _merge_safety_checks,
    _normalize_review_response,
    _review_from_ai_json,
    _review_from_ai_text,
    review_code,
)


def _review_with_bug(severity: str, risk_score: int = 15) -> ReviewResponse:
    return ReviewResponse(
        summary="Test review",
        risk_score=risk_score,
        bugs=[
            BugFinding(
                title=f"{severity} test issue",
                severity=severity,
                explanation="Test issue explanation.",
                suggested_fix="Fix the test issue.",
            )
        ],
        improvements=["Improve validation."],
        test_cases=["Test invalid input."],
        fixed_code=None,
        used_ai=True,
    )


def test_critical_bug_forces_risk_score_floor():
    review = _normalize_review_response(_review_with_bug("Critical", 15))

    assert review.risk_score >= 70


def test_high_bug_forces_risk_score_floor():
    review = _normalize_review_response(_review_with_bug("High", 15))

    assert review.risk_score >= 50


def test_fallback_placeholder_is_not_merged_with_real_ai_bugs():
    ai_review = _review_with_bug("Critical", 15)
    fallback_review = _fallback_review(
        ReviewRequest(
            language="Python",
            code="def add(a, b):\n    return a + b",
            focus="bugs",
        )
    )

    merged = _merge_safety_checks(ai_review, fallback_review)
    titles = [bug.title for bug in merged.bugs]

    assert "No critical issue detected by fallback engine" not in titles
    assert any("critical test issue" in title.lower() for title in titles)
    assert merged.risk_score >= 70


def test_ai_failure_uses_clean_fallback_summary(monkeypatch):
    class FailingCompletions:
        def create(self, **kwargs):
            raise ValueError("invalid json")

    class FailingChat:
        completions = FailingCompletions()

    class FailingClient:
        def __init__(self, **kwargs):
            self.chat = FailingChat()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("app.services.reviewer.OpenAI", FailingClient)
    _clear_review_cache()

    review = asyncio.run(
        review_code(
            ReviewRequest(
                language="Python",
                code='API_KEY = "demo"\nquery = "SELECT * FROM users WHERE id = " + user_id',
                focus="bugs, security",
            )
        )
    )

    assert review.used_ai is False
    assert review.review_source == "fallback_after_ai_error"
    assert "invalid json" not in review.summary.lower()
    assert "ai review was unavailable" not in review.summary.lower()
    assert "fallback review completed for python" in review.summary.lower()


def test_ai_uses_local_findings_context_when_raw_code_attempts_fail(monkeypatch):
    calls = {"count": 0, "prompts": []}
    review_content = json.dumps(
        {
            "summary": "AI review completed using local safety findings.",
            "risk_score": 80,
            "bugs": [
                {
                    "title": "Hardcoded secret in source code",
                    "severity": "High",
                    "explanation": "A secret is stored directly in code.",
                    "suggested_fix": "Move it to an environment variable.",
                }
            ],
            "improvements": ["Keep sensitive configuration outside source code."],
            "test_cases": ["Test that secrets are loaded from environment variables."],
            "fixed_code": None,
        }
    )
    language_content = json.dumps(
        {
            "detected_language": "Java",
            "confidence": 0.99,
            "evidence": "The code uses Java String declarations.",
        }
    )

    class FakeMessage:
        def __init__(self, content: str):
            self.content = content

    class FakeChoice:
        def __init__(self, content: str):
            self.message = FakeMessage(content)

    class FakeCompletion:
        def __init__(self, content: str):
            self.choices = [FakeChoice(content)]

    class FlakyCompletions:
        def create(self, **kwargs):
            calls["count"] += 1
            calls["prompts"].append(kwargs["messages"][-1]["content"])
            if calls["count"] == 1:
                return FakeCompletion(language_content)
            if calls["count"] < 4:
                raise ValueError("raw review failed")
            return FakeCompletion(review_content)

    class FlakyChat:
        completions = FlakyCompletions()

    class FlakyClient:
        def __init__(self, **kwargs):
            self.chat = FlakyChat()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("app.services.reviewer.OpenAI", FlakyClient)
    _clear_review_cache()

    review = asyncio.run(
        review_code(
            ReviewRequest(
                language="Java",
                code='String adminPassword = "demo-admin-password";',
                focus="bugs, security",
            )
        )
    )

    assert calls["count"] == 4
    assert "Local safety findings:" in calls["prompts"][-1]
    assert review.used_ai is True
    assert review.review_source == "ai_from_local_findings"
    assert "local safety findings" in review.summary.lower()


def test_invalid_ai_json_text_is_still_used_with_structured_findings(monkeypatch):
    class TextMessage:
        content = "The code has a division by zero bug and should validate the denominator."

    class TextChoice:
        message = TextMessage()

    class TextCompletion:
        choices = [TextChoice()]

    class TextCompletions:
        def create(self, **kwargs):
            return TextCompletion()

    class TextChat:
        completions = TextCompletions()

    class TextClient:
        def __init__(self, **kwargs):
            self.chat = TextChat()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("app.services.reviewer.OpenAI", TextClient)
    _clear_review_cache()

    review = asyncio.run(
        review_code(
            ReviewRequest(
                language="Python",
                code="def divide(a, b):\n    return a / b\nprint(divide(10, 0))",
                focus="bugs",
            )
        )
    )

    assert review.used_ai is True
    assert review.review_source == "ai_text_repair"
    assert any(_bug_category(bug) == "division_by_zero" for bug in review.bugs)
    assert any("division by zero" in item.lower() for item in review.improvements)


def test_ai_prompt_uses_detected_language_when_selection_is_wrong():
    payload = ReviewRequest(
        language="Python",
        code="""public class LoanServiceTest {
    public static void main(String[] args) {
        System.out.println("demo");
    }
}""",
        focus="bugs, security",
    )

    prompt = _ai_user_prompt(payload)

    assert _detected_review_language(payload.language, payload.code) == "Java"
    assert "Selected language from UI:\nPython" in prompt
    assert "AI language detection step result:\nJava" in prompt
    assert "```Java" in prompt


def test_review_response_reports_selected_detected_and_reviewed_language():
    review = _fallback_review(
        ReviewRequest(
            language="Python",
            code="""public class Demo {
    public static void main(String[] args) {
        System.out.println("hello");
    }
}""",
            focus="bugs",
        )
    )

    assert review.selected_language == "Python"
    assert review.detected_language == "Java"
    assert review.reviewed_language == "Java"


def test_review_request_can_auto_detect_without_selected_language():
    review = _fallback_review(
        ReviewRequest(
            code="""const express = require('express');
const app = express();
app.get('/health', (req, res) => res.json({ status: 'ok' }));""",
            focus="bugs",
        )
    )

    assert review.selected_language is None
    assert review.detected_language == "JavaScript"
    assert review.reviewed_language == "JavaScript"


def test_ruby_sinatra_code_auto_detects_ruby_not_javascript():
    code = """require "sinatra"
require "sqlite3"
require "net/http"

JWT_SECRET = "demo-jwt-secret"

post "/login" do
  body = JSON.parse(request.body.read)
  database = SQLite3::Database.new("clinic.db")
  user = database.execute("SELECT id FROM users WHERE email = '#{body["email"]}'").first
  database.close
  { token: "#{JWT_SECRET}-#{user[0]}" }.to_json
end"""

    review = _fallback_review(ReviewRequest(code=code, focus="security"))

    assert _detected_review_language("Auto", code) == "Ruby"
    assert review.selected_language is None
    assert review.detected_language == "Ruby"
    assert review.reviewed_language == "Ruby"


def test_auto_detect_removes_stale_language_mismatch_bug():
    review = ReviewResponse(
        summary="AI incorrectly reported a mismatch.",
        risk_score=90,
        bugs=[
            BugFinding(
                title="Language selection mismatch note",
                severity="Low",
                explanation="The code was reviewed as JavaScript.",
                suggested_fix="Choose JavaScript.",
            )
        ],
        improvements=[],
        test_cases=["Review with the right language."],
        fixed_code=None,
        used_ai=True,
    )

    normalized = _normalize_review_response(
        review,
        selected_language="Auto",
        code='require "sinatra"\npost "/login" do\nend',
    )

    assert normalized.bugs == []
    assert normalized.detected_language == "Ruby"
    assert normalized.reviewed_language == "Ruby"


def test_node_jwt_code_still_detects_javascript():
    code = """const jwt = require('jsonwebtoken');
const express = require('express');
const app = express();
app.post('/login', (req, res) => res.json({ token: jwt.sign({ id: 1 }, 'secret') }));"""

    assert _detected_review_language("Auto", code) == "JavaScript"


def test_d_vibed_code_auto_detects_d_not_javascript():
    code = """import vibe.d;
import std.stdio;
import std.process;

enum ADMIN_TOKEN = "demo-admin-token";

void login(HTTPServerRequest req, HTTPServerResponse res) {
    auto body = req.json;
    string email = body["email"].str;
    string sql = "SELECT id FROM users WHERE email = '" ~ email ~ "'";
    res.writeJsonBody(["ok": "true"]);
}

void main() {
    auto router = new URLRouter;
    router.post("/login", &login);
    listenHTTP(new HTTPServerSettings, router);
    runApplication();
}"""

    assert _detected_review_language("Auto", code) == "D"


def test_zig_code_auto_detects_zig_not_javascript():
    code = """const std = @import("std");

const ADMIN_TOKEN = "demo-admin-token";

const Request = struct {
    body: []const u8,
};

fn login(allocator: std.mem.Allocator, req: Request) !void {
    const email = try parseField(allocator, req.body, "email");
    const sql = try std.fmt.allocPrint(
        allocator,
        "SELECT id FROM users WHERE email = '{s}'",
        .{email},
    );
    _ = sql;
}

pub fn main() !void {
    var gpa = std.heap.GeneralPurposeAllocator(.{}){};
    const allocator = gpa.allocator();
    _ = allocator;
}"""

    assert _detected_review_language("Auto", code) == "Zig"


def test_smalltalk_code_auto_detects_smalltalk_not_sql():
    code = """Object subclass: #ClinicInsuranceService
    instanceVariableNames: ''
    classVariableNames: ''
    package: 'ClinicInsuranceRiskDemo'.

ClinicInsuranceService class >> loginEmail: email password: password
    | db sql rows user token |
    db := self openDatabase.

    sql := 'SELECT id, email, role FROM users WHERE email = '''
        , email
        , ''' AND password = '''
        , password
        , ''''.

    rows := db execute: sql.
    user := rows first.
    token := self createTokenFor: (user at: 'id') role: (user at: 'role').

    ^ Dictionary new
        at: 'token' put: token;
        yourself."""

    assert _detected_review_language("Auto", code) == "Smalltalk"


def test_elixir_code_auto_detects_elixir_not_python():
    code = """defmodule HospitalBillingService do
  @jwt_secret "demo-jwt-secret"

  def login(email, password) do
    query = "SELECT id FROM users WHERE email = '#{email}' AND password = '#{password}'"
    Postgrex.query!(conn, query, [])
  end
end"""

    assert _detected_review_language("Auto", code) == "Elixir"


def test_nim_jester_code_auto_detects_nim_not_sql():
    code = """import jester
import db_sqlite
import osproc

proc login(email: string, password: string): JsonNode =
  let query = "SELECT id, email FROM users WHERE email = '" & email & "'"
  let row = db.getRow(sql(query))
  return %*{"email": row[0]}

routes:
  post "/login":
    resp $login(@"email", @"password")

when isMainModule:
  runForever()"""

    assert _detected_review_language("Auto", code) == "Nim"


def test_crystal_kemal_code_auto_detects_crystal_not_ruby_or_sql():
    code = """require "kemal"
require "sqlite3"
require "json"
require "http/client"

post "/login" do |env|
  body = JSON.parse(env.request.body.not_nil!.gets_to_end)
  email = body["email"].as_s
  query = "SELECT id FROM users WHERE email = '#{email}'"
  result = database.query_one(query, as: {Int64})
end

Kemal.run"""

    assert _detected_review_language("Auto", code) == "Crystal"


def test_perl_mojolicious_code_auto_detects_perl_not_cpp_or_sql():
    code = """use strict;
use warnings;
use Mojolicious::Lite;
use DBI;

my $ADMIN_TOKEN = "demo-admin-token";

sub db_connect {
    return DBI->connect("dbi:SQLite:dbname=property.db", "", "");
}

post "/login" => sub {
    my $c = shift;
    my $email = $c->req->json->{email};
    my $dbh = db_connect();
    my $sql = "SELECT id FROM users WHERE email = '$email'";
    my $row = $dbh->selectrow_hashref($sql);
    return $c->render(json => { id => $row->{id} });
};

app->start;"""

    assert _detected_review_language("Auto", code) == "Perl"


def test_clojure_ring_compojure_code_auto_detects_clojure_not_sql():
    code = """(ns logistics-risk-platform.core
  (:require [ring.adapter.jetty :refer [run-jetty]]
            [ring.middleware.json :refer [wrap-json-body wrap-json-response]]
            [compojure.core :refer [defroutes GET POST DELETE]]
            [clojure.java.jdbc :as jdbc]
            [cheshire.core :as json]))

(def admin-token "demo-admin-token")

(defn login [email password]
  (let [sql (str "SELECT id, email, role FROM users WHERE email = '"
                 email "' AND password = '" password "'")
        user (first (jdbc/query db-spec [sql]))]
    {:user-id (:id user)}))

(defroutes app-routes
  (POST "/login" req
    (let [{:keys [email password]} (:body req)]
      (login email password))))

(def app
  (-> app-routes
      wrap-json-body
      wrap-json-response))

(defn -main []
  (run-jetty app {:port 3000 :join? false}))"""

    assert _detected_review_language("Auto", code) == "Clojure"


def test_lua_openresty_code_auto_detects_lua_not_sql():
    code = """local cjson = require "cjson"
local sqlite3 = require "lsqlite3"
local http = require "resty.http"

local DB_PATH = "rental_platform.db"

local function login()
    ngx.req.read_body()
    local body = cjson.decode(ngx.req.get_body_data())
    local email = body.email
    local sql = "SELECT id FROM users WHERE email = '" .. email .. "'"

    local conn = sqlite3.open(DB_PATH)
    for row in conn:nrows(sql) do
        ngx.say(cjson.encode(row))
        break
    end
end

local routes = {
    ["/login"] = login
}

local handler = routes[ngx.var.uri]
if handler then
    handler()
end"""

    assert _detected_review_language("Auto", code) == "Lua"


def test_lua_sql_string_concatenation_is_detected_as_sql_injection():
    review = _fallback_review(
        ReviewRequest(
            code="""local cjson = require "cjson"
local sqlite3 = require "lsqlite3"

local function login()
    local body = cjson.decode(ngx.req.get_body_data())
    local sql = "SELECT id FROM users WHERE email = '" .. body.email .. "'"
    local conn = sqlite3.open("app.db")
    for row in conn:nrows(sql) do
        ngx.say(cjson.encode(row))
    end
end""",
            focus="security",
        )
    )

    assert review.detected_language == "Lua"
    assert any(_bug_category(bug) == "sql_injection" and bug.severity == "Critical" for bug in review.bugs)
    assert review.risk_score >= 80


def test_lua_os_execute_command_construction_is_detected_as_command_injection():
    review = _fallback_review(
        ReviewRequest(
            code="""local cjson = require "cjson"

local function backup_database()
    local body = cjson.decode(ngx.req.get_body_data())
    local backup_name = body.backup_name
    local command = "sqlite3 app.db .dump > backups/" .. backup_name
    os.execute(command)
end""",
            focus="security",
        )
    )

    assert review.detected_language == "Lua"
    assert any(_bug_category(bug) == "command_injection" and bug.severity == "Critical" for bug in review.bugs)
    assert review.risk_score >= 80


def test_solidity_contract_auto_detects_solidity_not_javascript():
    code = """pragma solidity ^0.8.20;

contract Escrow {
    mapping(address => uint256) public balances;

    function deposit() external payable {
        balances[msg.sender] += msg.value;
    }
}"""

    assert _detected_review_language("Auto", code) == "Solidity"


def test_terraform_code_auto_detects_terraform_not_bash():
    code = '''provider "aws" {
  region = var.region
}

resource "aws_instance" "web" {
  ami           = var.ami_id
  instance_type = "t3.micro"
}

variable "region" {
  type = string
}'''

    assert _detected_review_language("Auto", code) == "Terraform"


def test_dart_flutter_code_auto_detects_dart_not_javascript():
    code = """import 'package:flutter/material.dart';

void main() {
  runApp(const MyApp());
}

class MyApp extends StatelessWidget {
  const MyApp({super.key});

  @override
  Widget build(BuildContext context) {
    return const MaterialApp(home: Text('Demo'));
  }
}"""

    assert _detected_review_language("Auto", code) == "Dart"


def test_ocaml_opium_code_auto_detects_ocaml_not_javascript():
    code = """open Lwt.Infix
open Opium
open Yojson.Safe
open Cohttp_lwt_unix

let db_path = "pharmacy_claims.db"
let admin_token = "demo-admin-token"

let login req =
  let body = Request.to_json_exn req in
  let email = body |> Util.member "email" |> Util.to_string in
  let sql =
    "SELECT id FROM users WHERE email = '"
    ^ email
    ^ "'"
  in
  json_response (`Assoc [ ("sql", `String sql) ])

let app =
  App.empty
  |> App.post "/login" login
  |> App.get "/prescriptions/:id" get_prescription

let () =
  App.run_command app"""

    assert _detected_review_language("Auto", code) == "OCaml"


def test_ocaml_fallback_detects_sql_command_and_path_risks():
    review = _fallback_review(
        ReviewRequest(
            code="""open Lwt.Infix
open Opium
open Yojson.Safe

let login req =
  let body = Request.to_json_exn req in
  let email = body |> Util.member "email" |> Util.to_string in
  let sql = "SELECT id FROM users WHERE email = '" ^ email ^ "'" in
  json_response (`Assoc [ ("sql", `String sql) ])

let backup_database req =
  let body = Request.to_json_exn req in
  let backup_name = body |> Util.member "backup_name" |> Util.to_string in
  let command = "sqlite3 app.db .dump > backups/" ^ backup_name in
  ignore (Sys.command command)

let upload req =
  let body = Request.to_json_exn req in
  let filename = body |> Util.member "filename" |> Util.to_string in
  let file_path = "uploads/" ^ filename in
  let oc = open_out file_path in
  output_string oc "demo";
  close_out oc""",
            focus="security",
        )
    )

    categories = {_bug_category(bug) for bug in review.bugs}
    assert review.detected_language == "OCaml"
    assert {"sql_injection", "command_injection", "path_traversal"}.issubset(categories)
    assert review.risk_score == 100


def test_groovy_code_auto_detects_groovy_not_python():
    code = """import groovy.json.JsonOutput
import groovy.sql.Sql

class HospitalInventoryService {
    static final String ADMIN_TOKEN = "demo-admin-token"

    static Map login(String username, String password) {
        def sql = Sql.newInstance("jdbc:sqlite:hospital_inventory.db", "", "", "org.sqlite.JDBC")
        def query = "SELECT id FROM users WHERE username = '${username}' AND password = '${password}'"
        def user = sql.firstRow(query)
        return [user_id: user.id]
    }
}"""

    assert _detected_review_language("Auto", code) == "Groovy"


def test_groovy_fallback_detects_sql_command_path_and_dynamic_execution():
    review = _fallback_review(
        ReviewRequest(
            code="""import groovy.sql.Sql

class Demo {
  static Map run(String username, String backupName, String filename, String configPath) {
    def query = "SELECT id FROM users WHERE username = '${username}'"
    def command = "sqlite3 app.db .dump > backups/${backupName}"
    command.execute()
    def filePath = "uploads/${filename}"
    new File(filePath).text = "demo"
    def shell = new GroovyShell()
    shell.evaluate(new File(configPath).text)
    return [ok: true]
  }
}""",
            focus="security",
        )
    )

    categories = {_bug_category(bug) for bug in review.bugs}
    assert review.detected_language == "Groovy"
    assert {"sql_injection", "command_injection", "path_traversal", "dynamic_execution"}.issubset(categories)
    assert review.risk_score == 100


def test_d_vibed_fallback_detects_sql_command_and_path_risks():
    review = _fallback_review(
        ReviewRequest(
            code="""import vibe.d;
import std.process;
import std.file;

enum DB_PATH = "app.db";
enum BACKUP_DIR = "backups";
enum UPLOAD_DIR = "uploads";

void backupDatabase(HTTPServerRequest req, HTTPServerResponse res) {
    string backupName = req.json["backup_name"].str;
    string command = "sqlite3 " ~ DB_PATH ~ " .dump > " ~ BACKUP_DIR ~ "/" ~ backupName;
    executeShell(command);
}

void upload(HTTPServerRequest req, HTTPServerResponse res) {
    string filename = req.json["filename"].str;
    string filePath = UPLOAD_DIR ~ "/" ~ filename;
    write(filePath, "demo");
}

void login(HTTPServerRequest req, HTTPServerResponse res) {
    string email = req.json["email"].str;
    string sql = "SELECT id FROM users WHERE email = '" ~ email ~ "'";
}""",
            focus="security",
        )
    )

    categories = {_bug_category(bug) for bug in review.bugs}
    assert review.detected_language == "D"
    assert {"sql_injection", "command_injection", "path_traversal"}.issubset(categories)
    assert review.risk_score == 100


def test_zig_fallback_detects_sql_command_path_and_raw_card_risks():
    review = _fallback_review(
        ReviewRequest(
            code="""const std = @import("std");

const PAYMENT_SECRET = "demo-payment-secret";
const UPLOAD_DIR = "uploads";

fn payOrder(allocator: std.mem.Allocator, req: Request) !void {
    const email = try parseField(allocator, req.body, "email");
    const card_number = try parseField(allocator, req.body, "card_number");
    const backup_name = try parseField(allocator, req.body, "backup_name");
    const filename = try parseField(allocator, req.body, "filename");

    const sql = try std.fmt.allocPrint(
        allocator,
        "SELECT id FROM users WHERE email = '{s}'",
        .{email},
    );

    const command = try std.fmt.allocPrint(
        allocator,
        "sqlite3 app.db .dump > backups/{s}",
        .{backup_name},
    );

    _ = try std.process.Child.run(.{
        .allocator = allocator,
        .argv = &[_][]const u8{ "sh", "-c", command },
    });

    const file_path = try std.fmt.allocPrint(allocator, "{s}/{s}", .{ UPLOAD_DIR, filename });
    try std.fs.cwd().writeFile(.{ .sub_path = file_path, .data = card_number });
    _ = sql;
}""",
            focus="security",
        )
    )

    categories = {_bug_category(bug) for bug in review.bugs}
    assert review.detected_language == "Zig"
    assert {"sql_injection", "hardcoded_secret", "command_injection", "path_traversal", "raw_card"}.issubset(categories)
    assert review.risk_score == 100


def test_smalltalk_fallback_detects_sql_command_path_and_dynamic_execution():
    review = _fallback_review(
        ReviewRequest(
            code="""Object subclass: #ClinicInsuranceService
    instanceVariableNames: ''
    classVariableNames: ''
    package: 'ClinicInsuranceRiskDemo'.

ClinicInsuranceService class >> adminToken
    ^ 'demo-admin-token'.

ClinicInsuranceService class >> loginEmail: email password: password
    | sql |
    sql := 'SELECT id FROM users WHERE email = ''' , email , ''' AND password = ''' , password , ''''.

ClinicInsuranceService class >> uploadMedicalDocumentForPatient: patientId filename: filename content: content
    | folder filePath |
    folder := self uploadDir , '/' , patientId.
    filePath := folder , '/' , filename.
    FileStream forceNewFileNamed: filePath do: [ :file | file nextPutAll: content ].

ClinicInsuranceService class >> backupDatabase: backupName
    | command |
    command := 'sqlite3 app.db .dump > backups/' , backupName.
    OSProcess command: command.

ClinicInsuranceService class >> importConfig: configPath
    | content config |
    content := FileStream readOnlyFileNamed: configPath do: [ :file | file contentsOfEntireFile ].
    config := Compiler evaluate: content.""",
            focus="security",
        )
    )

    categories = {_bug_category(bug) for bug in review.bugs}
    assert review.detected_language == "Smalltalk"
    assert {"sql_injection", "hardcoded_secret", "command_injection", "path_traversal", "dynamic_execution"}.issubset(categories)
    assert review.risk_score == 100


def test_erlang_code_auto_detects_erlang_not_elixir():
    code = """-module(payment_worker).
-export([start/0, charge/2]).

start() ->
    receive
        {charge, UserId, Amount} -> charge(UserId, Amount)
    end.

charge(UserId, Amount) ->
    {ok, UserId, Amount}."""

    assert _detected_review_language("Auto", code) == "Erlang"


def test_julia_code_auto_detects_julia_not_python():
    code = """using DataFrames

function calculate_total(items)
    total = 0
    for item in items
        total += item.price
    end
    println(total)
    return DataFrame(total = [total])
end"""

    assert _detected_review_language("Auto", code) == "Julia"


def test_r_code_auto_detects_r_not_python():
    code = """library(dplyr)

calculate_total <- function(items) {
  result <- data.frame(total = sum(items$price))
  return(result)
}

print(calculate_total(items))"""

    assert _detected_review_language("Auto", code) == "R"


def test_language_detection_prompt_uses_short_sample_for_large_code():
    code = "\n".join([f"line_{index}" for index in range(400)])
    prompt = _ai_language_detection_prompt(code)

    assert len(prompt) < len(code) + 800
    assert "middle omitted for language detection" in prompt


def test_code_sample_for_language_detection_keeps_head_and_tail():
    code = "\n".join([f"line_{index}" for index in range(200)])
    sample = _code_sample_for_language_detection(code, max_lines=20, max_chars=1000)

    assert "line_0" in sample
    assert "line_199" in sample
    assert "middle omitted for language detection" in sample


def test_auto_ai_prompt_does_not_send_local_detected_language_hint():
    code = """defmodule HospitalBillingService do
  def login(email, password) do
    "SELECT id FROM users WHERE email = '#{email}' AND password = '#{password}'"
  end
end"""

    prompt = _ai_user_prompt(ReviewRequest(code=code, focus="security"))

    assert "Manual language selection:\nNot used. The previous AI step detected the pasted code language." in prompt
    assert "AI language detection step result:\nAI must detect this from the pasted code." in prompt
    assert "```text" in prompt
    assert "```Python" not in prompt


def test_ai_json_language_detection_is_used_in_response():
    review = _review_from_ai_json(
        {
            "summary": "AI detected Ruby and reviewed the Sinatra code.",
            "detected_language": "Ruby",
            "reviewed_language": "Ruby",
            "risk_score": 80,
            "bugs": [
                {
                    "title": "SQL injection",
                    "severity": "Critical",
                    "explanation": "The SQL query interpolates user input.",
                    "suggested_fix": "Use parameterized queries.",
                }
            ],
            "improvements": ["Use safer database access patterns."],
            "test_cases": ["Test SQL injection payloads are rejected."],
            "fixed_code": None,
        },
        ReviewRequest(
            code='require "sinatra"\npost "/login" do\nend',
            focus="security",
        ),
    )

    assert review.used_ai is True
    assert review.detected_language == "Ruby"
    assert review.reviewed_language == "Ruby"


def test_ai_summary_language_corrects_conflicting_detected_metadata():
    review = _review_from_ai_json(
        {
            "summary": "The Elixir code contains critical SQL injection and command injection issues.",
            "detected_language": "Python",
            "reviewed_language": "Python",
            "risk_score": 100,
            "bugs": [
                {
                    "title": "SQL injection",
                    "severity": "Critical",
                    "explanation": "The SQL query interpolates user input.",
                    "suggested_fix": "Use parameterized queries.",
                }
            ],
            "improvements": ["Use Postgrex parameters."],
            "test_cases": ["Test SQL injection payloads are rejected."],
            "fixed_code": None,
        },
        ReviewRequest(
            code='defmodule HospitalBillingService do\n  def login(email), do: email\nend',
            focus="security",
        ),
    )

    assert review.detected_language == "Elixir"
    assert review.reviewed_language == "Elixir"


def test_ai_summary_language_corrects_conflicting_perl_metadata():
    review = _review_from_ai_json(
        {
            "summary": "The Perl application has SQL injection and command injection risks.",
            "detected_language": "C++",
            "reviewed_language": "C++",
            "risk_score": 100,
            "bugs": [
                {
                    "title": "SQL injection",
                    "severity": "Critical",
                    "explanation": "The SQL query interpolates user input.",
                    "suggested_fix": "Use DBI placeholders.",
                }
            ],
            "improvements": ["Use prepared statements."],
            "test_cases": ["Test SQL injection payloads are rejected."],
            "fixed_code": None,
        },
        ReviewRequest(
            code='use strict;\nuse Mojolicious::Lite;\nmy $sql = "SELECT id FROM users WHERE email = $email";\napp->start;',
            focus="security",
        ),
    )

    assert review.detected_language == "Perl"
    assert review.reviewed_language == "Perl"


def test_malformed_ai_json_summary_is_extracted_without_raw_json_improvement():
    content = '{"summary":"The Perl application has critical SQL injection risks.","detected_language":"C++","bugs":['
    review = _review_from_ai_text(
        content,
        ReviewRequest(
            code='use strict;\nuse Mojolicious::Lite;\nmy $sql = "SELECT id FROM users WHERE email = $email";\napp->start;',
            focus="security",
        ),
        "C++",
    )

    assert review.detected_language == "Perl"
    assert review.reviewed_language == "Perl"
    assert "perl application" in review.summary.lower()
    assert all('"summary"' not in improvement for improvement in review.improvements)


def test_ai_sql_language_metadata_is_corrected_for_clojure_application_code():
    review = _review_from_ai_json(
        {
            "summary": "AI review completed. The code has SQL injection and command injection risks.",
            "detected_language": "SQL",
            "reviewed_language": "SQL",
            "risk_score": 100,
            "bugs": [
                {
                    "title": "SQL injection",
                    "severity": "Critical",
                    "explanation": "The query concatenates user input.",
                    "suggested_fix": "Use parameterized queries.",
                }
            ],
            "improvements": ["Validate route input."],
            "test_cases": ["Test SQL injection payloads are rejected."],
            "fixed_code": None,
        },
        ReviewRequest(
            code="""(ns logistics-risk-platform.core
  (:require [compojure.core :refer [defroutes POST]]
            [clojure.java.jdbc :as jdbc]))

(defn login [email]
  (let [sql (str "SELECT id FROM users WHERE email = '" email "'")]
    (first (jdbc/query db-spec [sql]))))

(defroutes app-routes
  (POST "/login" req (login (:email (:body req)))))""",
            focus="security",
        ),
    )

    assert review.detected_language == "Clojure"
    assert review.reviewed_language == "Clojure"
    assert review.language_detection_confidence == 98
    assert "Clojure" in review.language_detection_evidence


def test_ai_sql_language_metadata_is_corrected_for_lua_openresty_application_code():
    review = _review_from_ai_json(
        {
            "summary": "AI review completed. The code has SQL injection and command injection risks.",
            "detected_language": "SQL",
            "reviewed_language": "SQL",
            "risk_score": 100,
            "bugs": [
                {
                    "title": "SQL injection",
                    "severity": "Critical",
                    "explanation": "The query concatenates user input.",
                    "suggested_fix": "Use parameterized queries.",
                }
            ],
            "improvements": ["Validate route input."],
            "test_cases": ["Test SQL injection payloads are rejected."],
            "fixed_code": None,
        },
        ReviewRequest(
            code="""local cjson = require "cjson"
local sqlite3 = require "lsqlite3"
local http = require "resty.http"

local function login()
    ngx.req.read_body()
    local body = cjson.decode(ngx.req.get_body_data())
    local sql = "SELECT id FROM users WHERE email = '" .. body.email .. "'"
    local conn = sqlite3.open("app.db")
    for row in conn:nrows(sql) do
        ngx.say(cjson.encode(row))
    end
end""",
            focus="security",
        ),
    )

    assert review.detected_language == "Lua"
    assert review.reviewed_language == "Lua"
    assert review.language_detection_confidence == 98
    assert "Lua" in review.language_detection_evidence


def test_ai_sql_language_metadata_is_corrected_for_nim_application_code():
    review = _review_from_ai_json(
        {
            "summary": "AI review completed. The code has SQL injection and command injection risks.",
            "detected_language": "SQL",
            "reviewed_language": "SQL",
            "risk_score": 100,
            "bugs": [
                {
                    "title": "SQL injection",
                    "severity": "Critical",
                    "explanation": "The query concatenates user input.",
                    "suggested_fix": "Use parameterized queries.",
                }
            ],
            "improvements": ["Validate route input."],
            "test_cases": ["Test SQL injection payloads are rejected."],
            "fixed_code": None,
        },
        ReviewRequest(
            code="""import jester
import db_sqlite

proc login(email: string): JsonNode =
  let query = "SELECT id FROM users WHERE email = '" & email & "'"
  let row = db.getRow(sql(query))
  return %*{"id": row[0]}

routes:
  post "/login":
    resp $login(@"email")""",
            focus="security",
        ),
    )

    assert review.detected_language == "Nim"
    assert review.reviewed_language == "Nim"


def test_ai_language_detection_overrides_local_hint():
    review = _normalize_review_response(
        ReviewResponse(
            summary="AI detected a custom language.",
            risk_score=30,
            bugs=[],
            improvements=["Review syntax manually."],
            test_cases=["Test the main flow."],
            fixed_code=None,
            used_ai=True,
            detected_language="Ruby",
            reviewed_language="Ruby",
        ),
        selected_language="Auto",
        code="const express = require('express');",
    )

    assert review.detected_language == "JavaScript"
    assert review.reviewed_language == "JavaScript"


def test_strong_syntax_detection_skips_extra_ai_language_call(monkeypatch):
    calls = {"count": 0, "prompts": []}
    review_content = json.dumps(
        {
            "summary": "The OCaml Opium application has unsafe SQL construction.",
            "detected_language": "JavaScript",
            "reviewed_language": "JavaScript",
            "risk_score": 80,
            "bugs": [
                {
                    "title": "SQL injection",
                    "severity": "Critical",
                    "explanation": "The query concatenates user input.",
                    "suggested_fix": "Use parameterized queries.",
                }
            ],
            "improvements": ["Use safer database access."],
            "test_cases": ["Test SQL injection payloads are rejected."],
            "fixed_code": None,
        }
    )

    class FakeMessage:
        def __init__(self, content: str):
            self.content = content

    class FakeChoice:
        def __init__(self, content: str):
            self.message = FakeMessage(content)

    class FakeCompletion:
        def __init__(self, content: str):
            self.choices = [FakeChoice(content)]

    class FakeCompletions:
        def create(self, **kwargs):
            calls["count"] += 1
            calls["prompts"].append(kwargs["messages"][-1]["content"])
            return FakeCompletion(review_content)

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        def __init__(self, **kwargs):
            self.chat = FakeChat()

    code = """open Lwt.Infix
open Opium
open Yojson.Safe

let login req =
  let body = Request.to_json_exn req in
  let email = body |> Util.member "email" |> Util.to_string in
  let sql = "SELECT id FROM users WHERE email = '" ^ email ^ "'" in
  json_response (`Assoc [ ("sql", `String sql) ])

let app =
  App.empty
  |> App.post "/login" login"""

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("app.services.reviewer.OpenAI", FakeClient)
    _clear_review_cache()

    review = asyncio.run(review_code(ReviewRequest(code=code, focus="security")))

    assert calls["count"] == 1
    assert "Detect the main programming language" not in calls["prompts"][0]
    assert "AI language detection step result:\nOCaml" in calls["prompts"][0]
    assert review.detected_language == "OCaml"
    assert review.reviewed_language == "OCaml"


def test_safe_retry_prompt_redacts_dangerous_literals():
    payload = ReviewRequest(
        language="Java",
        code='service.backupDatabase("backup.sql; rm -rf important_folder");\nADMIN_KEY = "demo-admin-key";',
        focus="security",
    )

    prompt = _ai_user_prompt(payload, retry_safe_mode=True)

    assert "rm -rf important_folder" not in prompt
    assert "[dangerous shell command omitted]" in prompt
    assert "[redacted-demo-secret]" in prompt


def test_local_findings_prompt_requires_null_fixed_code():
    payload = ReviewRequest(
        language="Python",
        code="def divide(a, b):\n    return a / b\nprint(divide(10, 0))",
        focus="bugs",
    )
    safety_review = _fallback_review(payload)
    prompt = _ai_local_findings_prompt(payload, safety_review)

    assert "Always set fixed_code to null." in prompt


def test_duplicate_test_cases_are_removed():
    review = ReviewResponse(
        summary="Test review",
        risk_score=30,
        bugs=[],
        improvements=["Improve validation.", "Improve validation."],
        test_cases=[
            "Test invalid input.",
            "Test invalid input.",
            "  Test invalid input.  ",
            "Test valid input.",
        ],
        fixed_code=None,
        used_ai=True,
    )

    normalized = _normalize_review_response(review)

    assert normalized.test_cases == ["Test invalid input.", "Test valid input."]
    assert normalized.improvements == ["Improve validation."]


def test_discount_sample_gets_fallback_runtime_and_business_rule_findings():
    review = _fallback_review(
        ReviewRequest(
            language="Python",
            code="""def calculate_discount(price, discount):
    final_price = price - (price * discount)

    if final_price < 0:
        return 0

    return final_price

print(calculate_discount(100, 1.5))
print(calculate_discount("100", 0.2))""",
            focus="bugs, business logic",
        )
    )

    finding_text = " ".join(
        [bug.title + " " + bug.explanation + " " + bug.suggested_fix for bug in review.bugs]
        + review.improvements
    ).lower()

    assert "typeerror" in finding_text or "type error" in finding_text
    assert "discount range" in finding_text
    assert review.risk_score >= 50


def test_duplicate_hardcoded_secret_fallback_is_removed_when_ai_reports_secret():
    ai_review = ReviewResponse(
        summary="AI found a secret.",
        risk_score=70,
        bugs=[
            BugFinding(
                title="Hardcoded API key",
                severity="High",
                explanation="A secret is stored in source code.",
                suggested_fix="Move it to an environment variable.",
            )
        ],
        improvements=[],
        test_cases=["Test that the API key is loaded from the environment."],
        fixed_code=None,
        used_ai=True,
    )
    fallback_review = _fallback_review(
        ReviewRequest(language="Python", code='API_KEY = "demo"\nprint(API_KEY)', focus="security")
    )

    merged = _merge_safety_checks(ai_review, fallback_review)

    assert sum(1 for bug in merged.bugs if _bug_category(bug) == "hardcoded_secret") == 1


def test_duplicate_type_error_fallback_is_removed_when_ai_reports_type_mismatch():
    ai_review = ReviewResponse(
        summary="AI found type mismatch.",
        risk_score=60,
        bugs=[
            BugFinding(
                title="Type mismatch in arithmetic calculation",
                severity="High",
                explanation="The function can receive a string and then perform arithmetic.",
                suggested_fix="Validate numeric inputs.",
            )
        ],
        improvements=[],
        test_cases=["Test string price input raises a clear validation error."],
        fixed_code=None,
        used_ai=True,
    )
    fallback_review = _fallback_review(
        ReviewRequest(
            language="Python",
            code='def total(price):\n    return price * 2\nprint(total("100"))',
            focus="bugs",
        )
    )

    merged = _merge_safety_checks(ai_review, fallback_review)

    assert sum(1 for bug in merged.bugs if _bug_category(bug) == "type_mismatch") == 1


def test_duplicate_path_traversal_fallback_is_removed_when_ai_reports_node_path_traversal():
    code = """const fs = require('fs');
const path = require('path');
app.post('/export', (req, res) => {
  const { filename } = req.body;
  fs.writeFileSync(path.join(__dirname, 'exports', filename), 'data');
  res.json({ ok: true });
});"""
    ai_review = ReviewResponse(
        summary="AI found path traversal.",
        risk_score=80,
        bugs=[
            BugFinding(
                title="Path Traversal Vulnerability",
                severity="Critical",
                explanation="fs.writeFileSync uses a user-controlled filename in path.join.",
                suggested_fix="Validate filenames and keep writes inside an allow-listed directory.",
            )
        ],
        improvements=[],
        test_cases=["Test ../ filenames are rejected."],
        fixed_code=None,
        used_ai=True,
    )
    fallback_review = _fallback_review(ReviewRequest(language="JavaScript", code=code, focus="security"))

    merged = _merge_safety_checks(ai_review, fallback_review, "JavaScript", code)

    assert sum(1 for bug in merged.bugs if _bug_category(bug) == "path_traversal") == 1
    assert any(_bug_category(bug) == "event_loop_blocking" for bug in merged.bugs)


def test_generic_test_cases_are_removed_when_specific_tests_exist():
    review = ReviewResponse(
        summary="Specific tests exist.",
        risk_score=80,
        bugs=[BugFinding(title="SQL injection", severity="Critical", explanation="Bad SQL.", suggested_fix="Use parameters.")],
        improvements=[],
        test_cases=[
            "Test normal valid input.",
            "Test invalid data type input.",
            "Test malicious SQL input is rejected.",
        ],
        fixed_code=None,
        used_ai=True,
    )

    normalized = _normalize_review_response(review)

    assert normalized.test_cases == ["Test malicious SQL input is rejected."]


def test_sql_injection_local_rule_creates_critical_severity():
    review = _fallback_review(
        ReviewRequest(
            language="Python",
            code='query = "SELECT * FROM users WHERE email = \'" + email + "\'"',
            focus="security",
        )
    )

    assert any(_bug_category(bug) == "sql_injection" and bug.severity == "Critical" for bug in review.bugs)
    assert review.risk_score >= 80


def test_requests_post_without_timeout_is_detected():
    review = _fallback_review(
        ReviewRequest(
            language="Python",
            code='import requests\nresponse = requests.post("https://api.example.com/pay", json=payload)',
            focus="reliability",
        )
    )

    assert any(_bug_category(bug) == "request_timeout" for bug in review.bugs)


def test_requests_post_without_exception_handling_is_detected():
    review = _fallback_review(
        ReviewRequest(
            language="Python",
            code='import requests\nresponse = requests.post("https://api.example.com/pay", json=payload)',
            focus="reliability",
        )
    )

    assert any(_bug_category(bug) == "request_exception" for bug in review.bugs)


def test_datetime_inside_json_dumps_is_detected():
    review = _fallback_review(
        ReviewRequest(
            language="Python",
            code="import json\nfrom datetime import datetime\ndata = {'created_at': datetime.utcnow()}\njson.dumps(data)",
            focus="bugs",
        )
    )

    assert any(_bug_category(bug) == "datetime_json" for bug in review.bugs)


def test_delete_user_connection_leak_is_detected():
    review = _fallback_review(
        ReviewRequest(
            language="Python",
            code="""def delete_user(user_id):
    conn = sqlite3.connect("app.db")
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        return False
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    return True""",
            focus="bugs",
        )
    )

    assert any(_bug_category(bug) == "db_connection" for bug in review.bugs)


def test_unchecked_fetchone_is_detected_when_another_fetchone_has_guard():
    review = _fallback_review(
        ReviewRequest(
            language="Python",
            code="""def create_order(user_id):
    conn = sqlite3.connect("shop.db")
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return user["email"]

def delete_user(user_id):
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        return False
    return True""",
            focus="bugs",
        )
    )

    assert any(_bug_category(bug) == "collection_none" for bug in review.bugs)


def test_negative_refund_amount_is_detected():
    review = _fallback_review(
        ReviewRequest(
            language="Python",
            code="""def refund_payment(payment_id, amount):
    refund_amount = -amount
    return gateway.refund(payment_id, refund_amount)""",
            focus="business logic",
        )
    )

    assert any(_bug_category(bug) == "negative_payment" for bug in review.bugs)


def test_risk_score_becomes_100_for_multiple_critical_security_payment_issues():
    review = _fallback_review(
        ReviewRequest(
            language="Python",
            code="""PAYMENT_TOKEN = "demo"
card_number = request["card_number"]
query = "SELECT * FROM payments WHERE card = '" + card_number + "'"
open(os.path.join("/tmp/uploads", request["filename"]), "w").write("x")""",
            focus="security, payments",
        )
    )

    categories = {_bug_category(bug) for bug in review.bugs}
    assert {"sql_injection", "raw_card", "hardcoded_secret"}.issubset(categories)
    assert review.risk_score == 100


def test_javascript_express_selected_as_javascript_does_not_show_language_mismatch():
    review = ReviewResponse(
        summary="AI incorrectly reported a mismatch.",
        risk_score=90,
        bugs=[
            BugFinding(
                title="Language Mismatch: Code is Node.js, not Python",
                severity="Critical",
                explanation="The code uses Express and require.",
                suggested_fix="Select JavaScript.",
            )
        ],
        improvements=[],
        test_cases=["Test Express route behavior."],
        fixed_code=None,
        used_ai=True,
    )

    normalized = _normalize_review_response(
        review,
        selected_language="JavaScript",
        code="const express = require('express'); const app = express(); app.post('/orders', handler);",
    )

    assert all(_bug_category(bug) != "language_mismatch" for bug in normalized.bugs)


def test_language_mismatch_is_not_critical_when_it_remains():
    review = ReviewResponse(
        summary="Wrong language selected.",
        risk_score=95,
        bugs=[
            BugFinding(
                title="Language Mismatch: Code is Node.js, not Python",
                severity="Critical",
                explanation="The code appears to use a different runtime.",
                suggested_fix="Select the closest matching language.",
            )
        ],
        improvements=[],
        test_cases=["Select the matching language and review again."],
        fixed_code=None,
        used_ai=True,
    )

    normalized = _normalize_review_response(review, selected_language="Python", code="const express = require('express');")

    assert normalized.bugs[0].severity == "Low"
    assert normalized.risk_score <= 25


def test_child_process_exec_with_user_input_is_critical_command_injection():
    review = _fallback_review(
        ReviewRequest(
            language="JavaScript",
            code="""const { exec } = require('child_process');
app.post('/backup', (req, res) => {
  const { backupName } = req.body;
  const command = "tar -czf backups/" + backupName + ".tgz data";
  exec(command, (err) => res.json({ ok: !err }));
});""",
            focus="security",
        )
    )

    assert any(_bug_category(bug) == "command_injection" and bug.severity == "Critical" for bug in review.bugs)


def test_user_controlled_webhook_url_in_axios_post_is_ssrf():
    review = _fallback_review(
        ReviewRequest(
            language="JavaScript",
            code="""const axios = require('axios');
app.post('/notify', async (req, res) => {
  const { webhookUrl } = req.body;
  await axios.post(webhookUrl, { orderId: req.body.orderId });
  res.json({ ok: true });
});""",
            focus="security",
        )
    )

    assert any(_bug_category(bug) == "ssrf" for bug in review.bugs)
    assert any(_bug_category(bug) == "request_timeout" for bug in review.bugs)
    assert any(_bug_category(bug) == "request_exception" for bug in review.bugs)


def test_fs_writefilesync_with_user_filename_is_path_traversal_or_unsafe_write():
    review = _fallback_review(
        ReviewRequest(
            language="JavaScript",
            code="""const fs = require('fs');
const path = require('path');
app.post('/export', (req, res) => {
  const { filename } = req.body;
  fs.writeFileSync(path.join(__dirname, 'exports', filename), 'data');
  res.json({ ok: true });
});""",
            focus="security",
        )
    )

    assert any(_bug_category(bug) == "path_traversal" for bug in review.bugs)
    assert any(_bug_category(bug) == "event_loop_blocking" for bug in review.bugs)


def test_javascript_hardcoded_jwt_admin_and_database_secrets_are_detected():
    review = _fallback_review(
        ReviewRequest(
            language="JavaScript",
            code="""const JWT_SECRET = "demo-jwt";
const ADMIN_KEY = "admin";
const database_password = "password";""",
            focus="security",
        )
    )

    assert any(_bug_category(bug) == "hardcoded_secret" and bug.severity == "High" for bug in review.bugs)


def test_javascript_raw_card_number_handling_is_detected():
    review = _fallback_review(
        ReviewRequest(
            language="JavaScript",
            code="""app.post('/pay', (req, res) => {
  const { cardNumber, cvv } = req.body;
  db.query(`INSERT INTO payments(card_number, cvv) VALUES (${cardNumber}, ${cvv})`);
  res.json({ ok: true });
});""",
            focus="security, payments",
        )
    )

    assert any(_bug_category(bug) == "raw_card" and bug.severity == "Critical" for bug in review.bugs)


def test_plain_text_password_sql_comparison_is_detected():
    review = _fallback_review(
        ReviewRequest(
            language="JavaScript",
            code="""app.post('/login', (req, res) => {
  const sql = "SELECT * FROM users WHERE email = '" + req.body.email + "' AND password = '" + req.body.password + "'";
  db.query(sql, (err, rows) => res.json(rows[0]));
});""",
            focus="security",
        )
    )

    assert any(_bug_category(bug) == "plaintext_password" and bug.severity == "High" for bug in review.bugs)


def test_javascript_off_by_one_loop_is_detected():
    review = _fallback_review(
        ReviewRequest(
            language="JavaScript",
            code="""function total(items) {
  let sum = 0;
  for (let i = 0; i <= items.length; i++) {
    sum += items[i].price;
  }
  return sum;
}""",
            focus="bugs",
        )
    )

    assert any(_bug_category(bug) == "off_by_one" for bug in review.bugs)


def test_rows_zero_without_check_is_detected():
    review = _fallback_review(
        ReviewRequest(
            language="JavaScript",
            code="""app.get('/users/:id', (req, res) => {
  db.query('SELECT * FROM users WHERE id = ' + req.params.id, (err, rows) => {
    res.json({ email: rows[0].email });
  });
});""",
            focus="bugs",
        )
    )

    assert any(_bug_category(bug) == "collection_none" for bug in review.bugs)


def test_db_query_without_error_handling_is_detected():
    review = _fallback_review(
        ReviewRequest(
            language="JavaScript",
            code="""app.get('/orders', (req, res) => {
  db.query('SELECT * FROM orders', (rows) => {
    res.json(rows);
  });
});""",
            focus="bugs",
        )
    )

    assert any(_bug_category(bug) == "db_query_error" for bug in review.bugs)


def test_node_security_payment_risk_score_becomes_100_for_multiple_critical_issues():
    review = _fallback_review(
        ReviewRequest(
            language="JavaScript",
            code="""const express = require('express');
const axios = require('axios');
const fs = require('fs');
const path = require('path');
const { exec } = require('child_process');
const JWT_SECRET = "demo-jwt";
const ADMIN_KEY = "admin";

app.post('/order', (req, res) => {
  const { email, cardNumber, cvv, webhookUrl, filename, backupName, amount } = req.body;
  const sql = `INSERT INTO payments(email, card_number, cvv, amount) VALUES ('${email}', '${cardNumber}', '${cvv}', ${amount})`;
  db.query(sql);
  db.query(`UPDATE inventory SET quantity = quantity - 1 WHERE email = '${email}'`);
  fs.writeFileSync(path.join(__dirname, 'exports', filename), cardNumber);
  exec("tar -czf backups/" + backupName + ".tgz exports");
  axios.post(webhookUrl, { cardNumber, amount });
  const refundAmount = -amount;
  for (let i = 0; i <= req.body.items.length; i++) {
    console.log(req.body.items[i].name);
  }
  res.json({ ok: true, refundAmount });
});""",
            focus="security, payments",
        )
    )

    categories = {_bug_category(bug) for bug in review.bugs}
    assert {"sql_injection", "command_injection", "raw_card", "ssrf", "path_traversal"}.issubset(categories)
    assert {"hardcoded_secret", "weak_admin", "negative_payment", "off_by_one", "transaction_rollback"}.issubset(categories)
    assert review.risk_score == 100
