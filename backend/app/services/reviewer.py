import asyncio
import difflib
import hashlib
import json
import logging
import os
import re
import time
from typing import Any, Dict

from openai import OpenAI

from app.schemas import ReviewRequest, ReviewResponse, BugFinding


logger = logging.getLogger(__name__)

_REVIEW_CACHE: dict[str, dict[str, Any]] = {}

_GENERIC_TEST_CASES = {
    "test normal valid input.",
    "test empty or missing input.",
    "test invalid data type input.",
    "test boundary values and large input.",
    "test error-handling behavior.",
}

_JAVASCRIPT_LANGUAGES = {"javascript", "js", "node", "node.js", "express", "typescript", "ts", "react"}

_NODE_MARKERS = (
    "require(",
    "axios",
    "fs.writefile",
    "fs.promises",
    "fs.readfile",
    "jwt.",
    "jsonwebtoken",
    "child_process",
    "module.exports",
    "app.get",
    "app.post",
    "router.get",
    "router.post",
)

_LANGUAGE_ALIASES = {
    "abap": "ABAP",
    "actionscript": "ActionScript",
    "ada": "Ada",
    "apex": "Apex",
    "as3": "ActionScript",
    "asm": "Assembly",
    "assembly": "Assembly",
    "awk": "AWK",
    "bash": "Bash",
    "ballerina": "Ballerina",
    "batch": "Batch",
    "bat": "Batch",
    "bazel": "Starlark",
    "bicep": "Bicep",
    "c": "C",
    "c#": "C#",
    "c++": "C++",
    "cadence": "Cadence",
    "cairo": "Cairo",
    "cap'n proto": "Cap'n Proto",
    "capnp": "Cap'n Proto",
    "cfml": "ColdFusion",
    "chapel": "Chapel",
    "circom": "Circom",
    "cl": "Common Lisp",
    "clojure": "Clojure",
    "clj": "Clojure",
    "cmake": "CMake",
    "cobol": "COBOL",
    "coffee": "CoffeeScript",
    "coffeescript": "CoffeeScript",
    "coldfusion": "ColdFusion",
    "common lisp": "Common Lisp",
    "compojure": "Clojure",
    "cpp": "C++",
    "css": "CSS",
    "crystal": "Crystal",
    "cue": "CUE",
    "cuda": "CUDA",
    "d": "D",
    "dart": "Dart",
    "delphi": "Pascal",
    "dhall": "Dhall",
    "dlang": "D",
    "dockerfile": "Dockerfile",
    "elixir": "Elixir",
    "elm": "Elm",
    "erlang": "Erlang",
    "f#": "F#",
    "forth": "Forth",
    "fortran": "Fortran",
    "fsharp": "F#",
    "flutter": "Dart",
    "gdscript": "GDScript",
    "gawk": "AWK",
    "glsl": "GLSL",
    "go": "Go",
    "golang": "Go",
    "graphql": "GraphQL",
    "groovy": "Groovy",
    "hack": "Hack",
    "haskell": "Haskell",
    "hcl": "Terraform",
    "haxe": "Haxe",
    "hlsl": "HLSL",
    "html": "HTML",
    "hy": "Hy",
    "ini": "INI",
    "janet": "Janet",
    "java": "Java",
    "javascript": "JavaScript",
    "jsonnet": "Jsonnet",
    "json": "JSON",
    "js": "JavaScript",
    "julia": "Julia",
    "kotlin": "Kotlin",
    "kt": "Kotlin",
    "latex": "LaTeX",
    "lisp": "Common Lisp",
    "lua": "Lua",
    "make": "Makefile",
    "makefile": "Makefile",
    "markdown": "Markdown",
    "matlab": "MATLAB",
    "md": "Markdown",
    "mermaid": "Mermaid",
    "mojolicious": "Perl",
    "moonscript": "MoonScript",
    "move": "Move",
    "nix": "Nix",
    "node": "JavaScript",
    "node.js": "JavaScript",
    "nginx lua": "Lua",
    "nim": "Nim",
    "objc": "Objective-C",
    "objective-c": "Objective-C",
    "ocaml": "OCaml",
    "odin": "Odin",
    "opencl": "OpenCL",
    "openresty": "Lua",
    "pascal": "Pascal",
    "perl": "Perl",
    "pike": "Pike",
    "php": "PHP",
    "plantuml": "PlantUML",
    "plsql": "PL/SQL",
    "pony": "Pony",
    "powershell": "PowerShell",
    "prolog": "Prolog",
    "proto": "Protocol Buffers",
    "protobuf": "Protocol Buffers",
    "ps1": "PowerShell",
    "puml": "PlantUML",
    "purescript": "PureScript",
    "python": "Python",
    "py": "Python",
    "q#": "Q#",
    "qml": "QML",
    "qsharp": "Q#",
    "r": "R",
    "racket": "Racket",
    "raku": "Raku",
    "reason": "ReasonML",
    "reasonml": "ReasonML",
    "rescript": "ReScript",
    "rest": "reStructuredText",
    "restructuredtext": "reStructuredText",
    "rst": "reStructuredText",
    "ruby": "Ruby",
    "rust": "Rust",
    "sas": "SAS",
    "scala": "Scala",
    "scheme": "Scheme",
    "sed": "sed",
    "shaderlab": "ShaderLab",
    "smalltalk": "Smalltalk",
    "sparql": "SPARQL",
    "sql": "SQL",
    "solidity": "Solidity",
    "starlark": "Starlark",
    "svelte": "Svelte",
    "swift": "Swift",
    "systemverilog": "SystemVerilog",
    "terraform": "Terraform",
    "tcl": "Tcl",
    "tex": "LaTeX",
    "thrift": "Thrift",
    "toml": "TOML",
    "ts": "TypeScript",
    "tsql": "T-SQL",
    "typescript": "TypeScript",
    "v": "V",
    "vb": "VB.NET",
    "vb.net": "VB.NET",
    "verilog": "Verilog",
    "vhdl": "VHDL",
    "visual basic": "VB.NET",
    "vlang": "V",
    "vue": "Vue",
    "wren": "Wren",
    "xml": "XML",
    "xquery": "XQuery",
    "yara": "YARA",
    "yaml": "YAML",
    "yml": "YAML",
    "zeek": "Zeek",
    "zig": "Zig",
}

_LANGUAGE_SIGNATURES: list[dict[str, Any]] = [
    {
        "language": "Pike",
        "confidence": 96,
        "evidence": "Pike syntax: import Stdio/Sql/Standards.JSON/Protocols.HTTP, constant declarations, mapping/array/object types, ([ ... ]) mappings, -> method calls, and Process.create_process.",
        "threshold": 6,
        "patterns": [
            (r"^\s*import\s+(?:stdio|sql|standards\.json|protocols\.http)\s*;", 5),
            (r"^\s*constant\s+\w+\s*=", 4),
            (r"\b(mapping|array|object|string|float|int)\s+\w+\s*(?:\(|=)", 3),
            (r"\(\[\s*(?:\"[^\"]+\"\s*:|[^\]]+)\]\)", 4),
            (r"->\s*\w+\s*\(", 3),
            (r"\bprocess\.create_process\s*\(", 5),
            (r"\bstandards\.json\.(?:encode|decode)\s*\(", 4),
            (r"\bprotocols\.http\.\w+", 4),
        ],
    },
    {
        "language": "Haxe",
        "confidence": 92,
        "evidence": "Haxe syntax: package/import haxe.*, class declarations, static function main, trace(), or typedef structures.",
        "threshold": 6,
        "patterns": [
            (r"^\s*import\s+haxe\.", 5),
            (r"\bclass\s+\w+\s*\{", 2),
            (r"\bstatic\s+function\s+main\s*\(", 5),
            (r"\btrace\s*\(", 3),
            (r"\btypedef\s+\w+\s*=", 3),
        ],
    },
    {
        "language": "V",
        "confidence": 90,
        "evidence": "V syntax: module declarations, fn main, struct fields with bare types, mut variables, or println.",
        "threshold": 6,
        "patterns": [
            (r"^\s*module\s+\w+", 4),
            (r"\bfn\s+main\s*\(", 5),
            (r"\bmut\s+\w+\s*:=", 3),
            (r"^\s*struct\s+\w+\s*\{", 3),
            (r"^\s*\w+\s+(?:string|int|bool|f64)\s*$", 2),
            (r"\bprintln\s*\(", 2),
        ],
    },
    {
        "language": "Odin",
        "confidence": 92,
        "evidence": "Odin syntax: package declarations, core:* imports, :: proc declarations, context, or defer.",
        "threshold": 6,
        "patterns": [
            (r"^\s*package\s+\w+", 3),
            (r"import\s+\"core:", 5),
            (r"\b\w+\s*::\s*proc\s*\(", 6),
            (r"\bcontext\b", 2),
            (r"\bdefer\s+", 2),
        ],
    },
    {
        "language": "Chapel",
        "confidence": 88,
        "evidence": "Chapel syntax: module/proc declarations, writeln, var/const typed declarations, forall, or use modules.",
        "threshold": 6,
        "patterns": [
            (r"^\s*module\s+\w+\s*\{", 4),
            (r"\bproc\s+main\s*\(", 5),
            (r"\bwriteln\s*\(", 4),
            (r"\b(var|const)\s+\w+\s*:\s*\w+", 3),
            (r"\bforall\s+", 3),
        ],
    },
    {
        "language": "Pony",
        "confidence": 88,
        "evidence": "Pony syntax: actor/class declarations, new create(env: Env), fun ref/box, and env.out.print.",
        "threshold": 6,
        "patterns": [
            (r"^\s*actor\s+\w+", 5),
            (r"\bnew\s+create\s*\(\s*env\s*:\s*env\s*\)", 5),
            (r"\bfun\s+(ref|box|val)\s+\w+", 4),
            (r"\benv\.out\.print\s*\(", 4),
            (r"\biso\b|\btrn\b|\bref\b", 2),
        ],
    },
    {
        "language": "Ballerina",
        "confidence": 90,
        "evidence": "Ballerina syntax: import ballerina/*, public function, returns error?, service/listener, or check expressions.",
        "threshold": 6,
        "patterns": [
            (r"^\s*import\s+ballerina/", 5),
            (r"\bpublic\s+function\s+\w+\s*\(", 4),
            (r"\breturns\s+error\?", 4),
            (r"\bservice\s+/?\w*\s+on\s+\w+", 5),
            (r"\bcheck\s+", 2),
        ],
    },
    {
        "language": "Q#",
        "confidence": 92,
        "evidence": "Q# syntax: namespace, open Microsoft.Quantum, operation/function declarations, Qubit, Unit, or quantum gates.",
        "threshold": 6,
        "patterns": [
            (r"^\s*namespace\s+[\w.]+\s*\{", 4),
            (r"^\s*open\s+microsoft\.quantum", 5),
            (r"\boperation\s+\w+\s*\([^)]*\)\s*:\s*\w+", 5),
            (r"\bqubit\b|\bunit\b", 3),
            (r"\b(h|x|z|cnot|measure)\s*\(", 2),
        ],
    },
    {
        "language": "Hack",
        "confidence": 92,
        "evidence": "Hack syntax: <?hh, Hack namespaces, typed functions/classes, vec/dict/keyset, or HHVM-style code.",
        "threshold": 6,
        "patterns": [
            (r"<\?hh", 6),
            (r"\bnamespace\s+[\w\\]+;", 3),
            (r"\bfunction\s+\w+\s*\([^)]*\)\s*:\s*\w+", 4),
            (r"\b(vec|dict|keyset)\s*\[", 3),
            (r"\b<<__\w+>>", 3),
        ],
    },
    {
        "language": "ActionScript",
        "confidence": 90,
        "evidence": "ActionScript syntax: package blocks, import flash.*, public class extends Sprite, function declarations, or trace().",
        "threshold": 6,
        "patterns": [
            (r"^\s*package\s*(?:[\w.]+)?\s*\{", 4),
            (r"^\s*import\s+flash\.", 5),
            (r"\bpublic\s+class\s+\w+\s+extends\s+\w+", 4),
            (r"\bpublic\s+function\s+\w+\s*\(", 4),
            (r"\btrace\s*\(", 3),
        ],
    },
    {
        "language": "CoffeeScript",
        "confidence": 88,
        "evidence": "CoffeeScript syntax: -> arrows, class extends, @property shorthand, existential operators, or module.exports without braces.",
        "threshold": 6,
        "patterns": [
            (r"^\s*class\s+\w+\s+extends\s+\w+", 4),
            (r"[-=]>\s*$", 4),
            (r"@\w+", 3),
            (r"\bmodule\.exports\s*=", 3),
            (r"\bthen\s+return\b|\bisnt\b|\bunless\b", 2),
        ],
    },
    {
        "language": "PureScript",
        "confidence": 90,
        "evidence": "PureScript syntax: module ... where, Effect imports, :: type signatures, do blocks, and log/Effect Unit main.",
        "threshold": 6,
        "patterns": [
            (r"^\s*module\s+[\w.]+\s+where\b", 4),
            (r"^\s*import\s+effect\b", 5),
            (r"\bmain\s*::\s*effect\s+unit", 5),
            (r"^\s*\w+\s*::\s*[^=\n]+", 3),
            (r"\bdo\s*$", 2),
        ],
    },
    {
        "language": "ReasonML",
        "confidence": 88,
        "evidence": "ReasonML syntax: let bindings with => arrows, type records, module declarations, switch, or Js.log.",
        "threshold": 6,
        "patterns": [
            (r"\blet\s+\w+\s*=\s*\([^)]*\)\s*=>", 4),
            (r"\btype\s+\w+\s*=\s*\{", 4),
            (r"\bmodule\s+\w+\s*=\s*\{", 4),
            (r"\bswitch\s*\(", 3),
            (r"\bjs\.log\s*\(", 3),
        ],
    },
    {
        "language": "ReScript",
        "confidence": 88,
        "evidence": "ReScript syntax: @react.component, Js.log, Belt APIs, let bindings, variants, and record types.",
        "threshold": 6,
        "patterns": [
            (r"@react\.component", 5),
            (r"\bjs\.log\s*\(", 4),
            (r"\bbelt\.", 4),
            (r"^\s*let\s+\w+\s*=", 2),
            (r"\btype\s+\w+\s*=\s*\{", 3),
        ],
    },
    {
        "language": "Fennel",
        "confidence": 88,
        "evidence": "Fennel syntax: fn/lambda forms with [] parameters, local bindings, each loops, and Lua interop forms.",
        "threshold": 6,
        "patterns": [
            (r"\(fn\s+\w*\s*\[[^\]]*\]", 5),
            (r"\(lambda\s+\[[^\]]*\]", 4),
            (r"\(local\s+\w+\s+", 3),
            (r"\(each\s+\[[^\]]+\]", 3),
            (r"\(print\s+", 2),
        ],
    },
    {
        "language": "Janet",
        "confidence": 88,
        "evidence": "Janet syntax: defn/def forms, fn with [] parameters, import/use forms, and Janet keywords.",
        "threshold": 6,
        "patterns": [
            (r"\(defn\s+\w+\s+\[[^\]]*\]", 5),
            (r"\(def\s+\w+\s+", 3),
            (r"\(fn\s+\[[^\]]*\]", 4),
            (r"\(import\s+\w+", 3),
            (r"\(printf?\s+", 2),
        ],
    },
    {
        "language": "Hy",
        "confidence": 86,
        "evidence": "Hy syntax: Lisp-style Python with defn, setv, import, print, and bracketed parameters.",
        "threshold": 6,
        "patterns": [
            (r"\(defn\s+\w+\s+\[[^\]]*\]", 5),
            (r"\(setv\s+\w+\s+", 4),
            (r"\(import\s+\w+", 3),
            (r"\(print\s+", 2),
            (r"\(for\s+\[[^\]]+\]", 3),
        ],
    },
    {
        "language": "MoonScript",
        "confidence": 84,
        "evidence": "MoonScript syntax: class blocks, new: => constructors, @property shorthand, and Lua-like exports.",
        "threshold": 6,
        "patterns": [
            (r"^\s*class\s+\w+", 4),
            (r"^\s*new:\s*=>", 5),
            (r"@\w+", 3),
            (r"^\s*\w+:\s*=>", 3),
            (r"\bexport\s+\w+", 2),
        ],
    },
    {
        "language": "Wren",
        "confidence": 86,
        "evidence": "Wren syntax: class declarations, static methods, System.print, construct new, and var fields.",
        "threshold": 6,
        "patterns": [
            (r"^\s*class\s+\w+\s*\{", 3),
            (r"\bstatic\s+\w+\s*\(", 3),
            (r"\bsystem\.print\s*\(", 5),
            (r"\bconstruct\s+new\s*\(", 4),
            (r"\bvar\s+\w+", 2),
        ],
    },
    {
        "language": "Move",
        "confidence": 90,
        "evidence": "Move syntax: address/module blocks, public fun, struct has key/store, signer, and u64 resources.",
        "threshold": 6,
        "patterns": [
            (r"\bmodule\s+0x[0-9a-f]+::\w+\s*\{", 6),
            (r"\bpublic\s+fun\s+\w+\s*\(", 5),
            (r"\bstruct\s+\w+\s+has\s+", 4),
            (r"\bsigner\b", 3),
            (r"\bu64\b|\bvector<", 2),
        ],
    },
    {
        "language": "Cadence",
        "confidence": 88,
        "evidence": "Cadence syntax: pub/access(all) contract/resource, fun declarations, auth accounts, and Flow-style resources.",
        "threshold": 6,
        "patterns": [
            (r"\bpub\s+contract\s+\w+\s*\{", 5),
            (r"\baccess\(all\)\s+(?:contract|resource|fun)\b", 5),
            (r"\bpub\s+resource\s+\w+", 4),
            (r"\bauth\(", 3),
            (r"\bsave\s*<-", 3),
        ],
    },
    {
        "language": "Cairo",
        "confidence": 90,
        "evidence": "Cairo syntax: %lang starknet, #[starknet::contract], felt252, fn declarations, or cairo modules.",
        "threshold": 6,
        "patterns": [
            (r"%lang\s+starknet", 6),
            (r"#\[starknet::contract\]", 6),
            (r"\bfelt252\b", 4),
            (r"\bfn\s+\w+\s*\(", 2),
            (r"\bmod\s+\w+\s*\{", 3),
        ],
    },
    {
        "language": "Circom",
        "confidence": 92,
        "evidence": "Circom syntax: pragma circom, template definitions, signal input/output, component main, or constraints.",
        "threshold": 6,
        "patterns": [
            (r"pragma\s+circom\b", 6),
            (r"\btemplate\s+\w+\s*\(", 5),
            (r"\bsignal\s+(input|output)\b", 4),
            (r"\bcomponent\s+main\s*=", 4),
            (r"<==|==>", 3),
        ],
    },
    {
        "language": "GLSL",
        "confidence": 90,
        "evidence": "GLSL shader syntax: #version, layout qualifiers, vec/mat types, gl_Position, in/out variables, or sampler uniforms.",
        "threshold": 6,
        "patterns": [
            (r"^\s*#version\s+\d+", 5),
            (r"\blayout\s*\(\s*location\s*=", 4),
            (r"\b(vec[234]|mat[234]|sampler2d)\b", 3),
            (r"\bgl_position\b|\bgl_fragcolor\b", 4),
            (r"\bvoid\s+main\s*\(\s*\)", 2),
        ],
    },
    {
        "language": "HLSL",
        "confidence": 90,
        "evidence": "HLSL shader syntax: cbuffer, Texture2D/SamplerState, float vectors, SV_* semantics, or register bindings.",
        "threshold": 6,
        "patterns": [
            (r"\bcbuffer\s+\w+", 5),
            (r"\btexture2d\b|\bsamplerstate\b", 4),
            (r"\bfloat[234](?:x[234])?\b", 3),
            (r":\s*sv_(target|position|vertexid)\b", 5),
            (r"\bregister\s*\(\s*[btus]\d+\s*\)", 3),
        ],
    },
    {
        "language": "CUDA",
        "confidence": 92,
        "evidence": "CUDA C/C++ syntax: __global__/__device__ kernels, threadIdx/blockIdx, cudaMalloc, or kernel launch <<< >>>.",
        "threshold": 6,
        "patterns": [
            (r"__global__\s+void\s+\w+\s*\(", 6),
            (r"\b(threadidx|blockidx|blockdim)\.", 5),
            (r"\bcudamalloc\s*\(|\bcudamemcpy\s*\(", 4),
            (r"<<<[^>]+>>>", 5),
            (r"^\s*#include\s*<cuda", 4),
        ],
    },
    {
        "language": "OpenCL",
        "confidence": 90,
        "evidence": "OpenCL C syntax: __kernel, __global pointers, get_global_id, cl_mem, or OpenCL API calls.",
        "threshold": 6,
        "patterns": [
            (r"__kernel\s+void\s+\w+\s*\(", 6),
            (r"__global\s+\w+\s*\*", 4),
            (r"\bget_global_id\s*\(", 5),
            (r"\bcl_mem\b|\bclenqueue", 3),
            (r"\bbarrier\s*\(\s*clk_", 3),
        ],
    },
    {
        "language": "ShaderLab",
        "confidence": 90,
        "evidence": "Unity ShaderLab syntax: Shader blocks, Properties/SubShader/Pass, CGPROGRAM/HLSLPROGRAM, or ENDCG.",
        "threshold": 6,
        "patterns": [
            (r"^\s*shader\s+\"[^\"]+\"\s*\{", 6),
            (r"\bproperties\s*\{", 4),
            (r"\bsubshader\s*\{", 4),
            (r"\bpass\s*\{", 2),
            (r"\bcgprogram\b|\bhlslprogram\b|\bendcg\b", 4),
        ],
    },
    {
        "language": "AWK",
        "confidence": 86,
        "evidence": "AWK syntax: BEGIN/END pattern blocks, FS/OFS, print fields like $1, or awk-style actions.",
        "threshold": 6,
        "patterns": [
            (r"^\s*begin\s*\{", 5),
            (r"^\s*end\s*\{", 4),
            (r"\b(fs|ofs)\s*=", 3),
            (r"\bprint\s+\$?\w*", 2),
            (r"\$\d+", 3),
        ],
    },
    {
        "language": "sed",
        "confidence": 80,
        "evidence": "sed script syntax: substitution commands, address ranges, labels, branches, print/delete commands.",
        "threshold": 6,
        "patterns": [
            (r"^\s*s(.).+\1.*\1[gip]*\s*$", 5),
            (r"^\s*\d+(?:,\d+)?[pd]\s*$", 3),
            (r"^\s*:\w+", 3),
            (r"^\s*[bt]\s+\w+", 3),
            (r"^\s*/[^/]+/[pd]\s*$", 3),
        ],
    },
    {
        "language": "Markdown",
        "confidence": 86,
        "evidence": "Markdown syntax: headings, fenced code blocks, links/images, blockquotes, or task/list markup.",
        "threshold": 6,
        "patterns": [
            (r"^\s*#{1,6}\s+\w+", 4),
            (r"```[\w-]*", 4),
            (r"\[[^\]]+\]\([^)]+\)", 3),
            (r"^\s*>\s+", 2),
            (r"^\s*[-*]\s+\[[ x]\]\s+", 3),
            (r"^\s*[-*]\s+\w+", 2),
        ],
    },
    {
        "language": "Mermaid",
        "confidence": 92,
        "evidence": "Mermaid diagram syntax: graph/flowchart/sequenceDiagram/classDiagram/stateDiagram blocks and arrows.",
        "threshold": 6,
        "patterns": [
            (r"^\s*(graph|flowchart)\s+(td|lr|rl|bt)\b", 6),
            (r"^\s*sequencediagram\b", 6),
            (r"^\s*classdiagram\b|^\s*statediagram", 5),
            (r"-->|---|--\|", 3),
            (r"\bparticipant\s+\w+", 3),
        ],
    },
    {
        "language": "PlantUML",
        "confidence": 94,
        "evidence": "PlantUML syntax: @startuml/@enduml, actor/participant declarations, arrows, or UML diagram blocks.",
        "threshold": 5,
        "patterns": [
            (r"@startuml", 6),
            (r"@enduml", 6),
            (r"^\s*(actor|participant|class|interface)\s+\w+", 3),
            (r"-->|->|<--", 3),
        ],
    },
    {
        "language": "LaTeX",
        "confidence": 92,
        "evidence": "LaTeX syntax: documentclass/usepackage, begin/end document, commands, environments, or math delimiters.",
        "threshold": 6,
        "patterns": [
            (r"\\documentclass(?:\[[^\]]+\])?\{", 6),
            (r"\\usepackage(?:\[[^\]]+\])?\{", 4),
            (r"\\begin\{document\}", 5),
            (r"\\end\{document\}", 5),
            (r"\\section\{", 2),
        ],
    },
    {
        "language": "reStructuredText",
        "confidence": 84,
        "evidence": "reStructuredText syntax: directives, roles, toctree/code-block, underlined headings, or Sphinx markup.",
        "threshold": 6,
        "patterns": [
            (r"^\s*\.\.\s+(code-block|toctree|note|warning)::", 5),
            (r":[\w-]+:`[^`]+`", 4),
            (r"^\s*={3,}\s*$|^\s*-{3,}\s*$", 2),
            (r"^\s*\.\.\s+\w+::", 3),
        ],
    },
    {
        "language": "Dhall",
        "confidence": 84,
        "evidence": "Dhall syntax: let/in expressions, typed records, Text/Natural/Bool types, merge, or constructors.",
        "threshold": 6,
        "patterns": [
            (r"^\s*let\s+\w+\s*=", 3),
            (r"\bin\s+\w+", 2),
            (r"\b(text|natural|bool|integer|double)\b", 3),
            (r"\{\s*\w+\s*:\s*\w+", 3),
            (r"\bmerge\s+", 3),
        ],
    },
    {
        "language": "Jsonnet",
        "confidence": 86,
        "evidence": "Jsonnet syntax: local bindings, std.* helpers, object inheritance, hidden fields, or function fields.",
        "threshold": 6,
        "patterns": [
            (r"^\s*local\s+\w+\s*=", 4),
            (r"\bstd\.\w+\s*\(", 4),
            (r"\+:\s*\{", 3),
            (r"\w+::\s*", 3),
            (r"\bfunction\s*\(", 3),
        ],
    },
    {
        "language": "Starlark",
        "confidence": 88,
        "evidence": "Starlark/Bazel syntax: load() statements, rule definitions, native.* calls, cc_binary, or select().",
        "threshold": 6,
        "patterns": [
            (r"^\s*load\s*\(\s*['\"]@", 5),
            (r"\bnative\.\w+\s*\(", 4),
            (r"\bcc_binary\s*\(|\bpy_library\s*\(", 4),
            (r"\bselect\s*\(\s*\{", 3),
            (r"^\s*def\s+\w+\s*\([^)]*\):", 2),
        ],
    },
    {
        "language": "CUE",
        "confidence": 86,
        "evidence": "CUE syntax: package declarations, #definitions, field constraints, unification, and typed values.",
        "threshold": 6,
        "patterns": [
            (r"^\s*package\s+\w+", 3),
            (r"^\s*#\w+\s*:\s*\{", 5),
            (r"^\s*\w+\s*:\s*(?:string|int|bool|number|\[)", 3),
            (r"\|\s*_\s*", 3),
            (r"\bclose\s*\(", 2),
        ],
    },
    {
        "language": "Bicep",
        "confidence": 90,
        "evidence": "Bicep syntax: param/var declarations, resource declarations with Azure type strings, modules, and outputs.",
        "threshold": 6,
        "patterns": [
            (r"^\s*param\s+\w+\s+\w+", 4),
            (r"^\s*resource\s+\w+\s+'microsoft\.", 6),
            (r"^\s*module\s+\w+\s+'[^']+'\s*=", 4),
            (r"^\s*output\s+\w+\s+\w+", 3),
            (r"\bresourcegroup\s*\(", 3),
        ],
    },
    {
        "language": "Thrift",
        "confidence": 88,
        "evidence": "Apache Thrift syntax: namespace, struct/service, numbered fields, required/optional fields, or throws clauses.",
        "threshold": 6,
        "patterns": [
            (r"^\s*namespace\s+\w+\s+[\w.]+", 4),
            (r"^\s*(struct|service|exception)\s+\w+\s*\{", 5),
            (r"^\s*\d+\s*:\s*(required|optional)?\s*\w+\s+\w+", 4),
            (r"\bthrows\s*\(", 3),
            (r"^\s*typedef\s+\w+\s+\w+", 2),
        ],
    },
    {
        "language": "Cap'n Proto",
        "confidence": 88,
        "evidence": "Cap'n Proto syntax: file ID, struct/interface declarations, fields with @ordinals, or using imports.",
        "threshold": 6,
        "patterns": [
            (r"^\s*@0x[0-9a-f]+;", 6),
            (r"^\s*struct\s+\w+\s*\{", 4),
            (r"^\s*interface\s+\w+\s*\{", 4),
            (r"\w+\s+@[0-9]+\s*:\s*\w+", 4),
            (r"^\s*using\s+\w+\s*=", 3),
        ],
    },
    {
        "language": "YARA",
        "confidence": 90,
        "evidence": "YARA rule syntax: rule blocks, strings and condition sections, $ identifiers, or meta fields.",
        "threshold": 6,
        "patterns": [
            (r"^\s*rule\s+\w+\s*(?::\s*[\w\s]+)?\s*\{", 6),
            (r"^\s*strings\s*:", 4),
            (r"^\s*condition\s*:", 4),
            (r"\$\w+\s*=", 3),
            (r"^\s*meta\s*:", 2),
        ],
    },
    {
        "language": "Zeek",
        "confidence": 86,
        "evidence": "Zeek script syntax: event handlers, module/export blocks, redef, notices, or Zeek-specific types.",
        "threshold": 6,
        "patterns": [
            (r"^\s*module\s+\w+\s*;", 4),
            (r"^\s*export\s*\{", 3),
            (r"^\s*event\s+\w+\s*\(", 5),
            (r"\bredef\s+", 4),
            (r"\bnotice::", 3),
        ],
    },
    {
        "language": "SPARQL",
        "confidence": 88,
        "evidence": "SPARQL syntax: PREFIX declarations, SELECT/ASK/CONSTRUCT queries, WHERE graph patterns, and ?variables.",
        "threshold": 6,
        "patterns": [
            (r"^\s*prefix\s+\w*:\s*<[^>]+>", 5),
            (r"\bselect\s+(?:distinct\s+)?\?\w+", 4),
            (r"\bwhere\s*\{", 4),
            (r"\?\w+\s+\?\w+\s+\?\w+", 3),
            (r"\bconstruct\s*\{|\bask\s*\{", 3),
        ],
    },
    {
        "language": "XQuery",
        "confidence": 88,
        "evidence": "XQuery syntax: xquery version, for/let/where/return expressions, doc() paths, and $ variables.",
        "threshold": 6,
        "patterns": [
            (r"^\s*xquery\s+version\s+['\"]", 6),
            (r"\bfor\s+\$\w+\s+in\s+", 4),
            (r"\breturn\s+\$\w+", 3),
            (r"\bdoc\s*\(", 3),
            (r"\bdeclare\s+(namespace|function)\b", 4),
        ],
    },
    {
        "language": "TypeScript",
        "confidence": 95,
        "evidence": "TypeScript syntax: interfaces, type aliases, typed parameters, generics, import type, or `as` type assertions.",
        "threshold": 6,
        "patterns": [
            (r"\binterface\s+\w+\s*\{", 5),
            (r"\btype\s+\w+\s*=", 4),
            (r"\bimport\s+type\b", 4),
            (r":\s*(string|number|boolean|unknown|any)\b", 3),
            (r"\bas\s+(?:const|string|number|boolean|\w+)", 2),
            (r"\breadonly\s+\w+\s*:", 2),
        ],
    },
    {
        "language": "C++",
        "confidence": 95,
        "evidence": "C++ syntax: iostream/vector includes, std::, using namespace std, templates, nullptr, cout, or class methods with ::.",
        "threshold": 6,
        "patterns": [
            (r"#include\s*<(?:iostream|vector|string|memory|map|unordered_map)>", 5),
            (r"\bstd::\w+", 5),
            (r"\busing\s+namespace\s+std\s*;", 4),
            (r"\btemplate\s*<", 4),
            (r"\bnullptr\b", 3),
            (r"\bcout\s*<<", 3),
            (r"\b\w+::\w+\s*\(", 3),
        ],
    },
    {
        "language": "C",
        "confidence": 94,
        "evidence": "C syntax: stdio/stdlib includes, int main, printf, malloc/free, struct declarations, or pointer-heavy C APIs.",
        "threshold": 6,
        "patterns": [
            (r"#include\s*<(?:stdio|stdlib|string|stdint|stdbool)\.h>", 5),
            (r"\bint\s+main\s*\([^)]*\)", 4),
            (r"\bprintf\s*\(", 3),
            (r"\bmalloc\s*\(|\bfree\s*\(", 3),
            (r"\bstruct\s+\w+\s*\{", 3),
            (r"\bchar\s+\*\w+", 2),
        ],
    },
    {
        "language": "C#",
        "confidence": 94,
        "evidence": "C# syntax: using System, namespace, Console.WriteLine, public class, async Task, or LINQ-style C# APIs.",
        "threshold": 6,
        "patterns": [
            (r"^\s*using\s+system\b", 5),
            (r"^\s*namespace\s+\w+", 4),
            (r"\bconsole\.writeline\s*\(", 4),
            (r"\b(public|private|protected)\s+class\s+\w+", 3),
            (r"\basync\s+task\b|\btask<", 3),
            (r"\bienumerable<", 3),
        ],
    },
    {
        "language": "Java",
        "confidence": 94,
        "evidence": "Java syntax: package/import java.*, public class, public static void main, System.out.println, or Java annotations.",
        "threshold": 6,
        "patterns": [
            (r"^\s*package\s+[\w.]+\s*;", 4),
            (r"^\s*import\s+java\.", 4),
            (r"\bpublic\s+class\s+\w+", 4),
            (r"\bpublic\s+static\s+void\s+main\s*\(", 5),
            (r"\bsystem\.out\.println\s*\(", 4),
            (r"@\w+\s*(?:\n|\r\n)\s*(?:public|private|protected)", 2),
        ],
    },
    {
        "language": "Kotlin",
        "confidence": 94,
        "evidence": "Kotlin syntax: fun main, data class, val/var declarations, nullable types, companion objects, or println.",
        "threshold": 6,
        "patterns": [
            (r"\bfun\s+main\s*\(", 5),
            (r"\bdata\s+class\s+\w+", 5),
            (r"\b(val|var)\s+\w+\s*[:=]", 3),
            (r"\bcompanion\s+object\b", 4),
            (r"\b\w+\?\s*=", 2),
            (r"\bprintln\s*\(", 2),
        ],
    },
    {
        "language": "Swift",
        "confidence": 93,
        "evidence": "Swift syntax: import SwiftUI/Foundation, let/var, func, guard let, optional types, or struct conforming to View.",
        "threshold": 6,
        "patterns": [
            (r"^\s*import\s+(swiftui|foundation)\b", 5),
            (r"\bfunc\s+\w+\s*\([^)]*\)\s*(?:->\s*\w+)?\s*\{", 4),
            (r"\bguard\s+let\b", 4),
            (r"\bstruct\s+\w+\s*:\s*view\b", 5),
            (r"\b(let|var)\s+\w+\s*[:=]", 2),
            (r"\bif\s+let\b", 2),
        ],
    },
    {
        "language": "Go",
        "confidence": 94,
        "evidence": "Go syntax: package main, import blocks, func declarations, fmt.Println, goroutines, or err != nil checks.",
        "threshold": 6,
        "patterns": [
            (r"^\s*package\s+\w+", 5),
            (r"\bfunc\s+\w+\s*\([^)]*\)", 4),
            (r"\bfmt\.println\s*\(", 4),
            (r"\bgo\s+func\b", 3),
            (r"\bif\s+err\s*!=\s*nil\b", 3),
            (r":=\s*", 2),
        ],
    },
    {
        "language": "Rust",
        "confidence": 94,
        "evidence": "Rust syntax: fn main, let mut, println!, match blocks, impl, Result<T>, or use crate/std imports.",
        "threshold": 6,
        "patterns": [
            (r"\bfn\s+main\s*\(", 5),
            (r"\blet\s+mut\s+\w+", 4),
            (r"\bprintln!\s*\(", 4),
            (r"\bmatch\s+\w+\s*\{", 3),
            (r"\bimpl\s+\w+", 3),
            (r"\bresult\s*<[^>]+>", 3),
            (r"^\s*use\s+(?:std|crate)::", 3),
        ],
    },
    {
        "language": "PHP",
        "confidence": 94,
        "evidence": "PHP syntax: <?php, $variables, namespace/use, function declarations, echo, or Laravel-style code.",
        "threshold": 6,
        "patterns": [
            (r"<\?php", 6),
            (r"\$\w+\s*=", 3),
            (r"^\s*namespace\s+[\w\\]+;", 4),
            (r"^\s*use\s+[\w\\]+;", 3),
            (r"\bfunction\s+\w+\s*\([^)]*\)\s*\{", 3),
            (r"\becho\s+", 2),
        ],
    },
    {
        "language": "PowerShell",
        "confidence": 94,
        "evidence": "PowerShell syntax: param blocks, Verb-Noun cmdlets, $env:, Write-Host, function blocks, or pipeline cmdlets.",
        "threshold": 6,
        "patterns": [
            (r"^\s*param\s*\(", 5),
            (r"\b(get|set|new|start|stop|invoke|write|select|where)-[a-z]+\b", 4),
            (r"\$env:\w+", 4),
            (r"\bwrite-host\b", 3),
            (r"^\s*function\s+[\w-]+\s*\{", 3),
            (r"\|\s*(where-object|select-object|foreach-object)\b", 3),
        ],
    },
    {
        "language": "Batch",
        "confidence": 92,
        "evidence": "Windows batch syntax: @echo off, setlocal, %VAR% expansion, goto labels, call, or .bat commands.",
        "threshold": 6,
        "patterns": [
            (r"^\s*@echo\s+off\b", 6),
            (r"^\s*setlocal\b", 4),
            (r"%\w+%", 3),
            (r"^\s*goto\s+:\w+", 3),
            (r"^\s*:\w+", 2),
            (r"^\s*call\s+", 2),
        ],
    },
    {
        "language": "CMake",
        "confidence": 96,
        "evidence": "CMake syntax: cmake_minimum_required, project(), add_executable/library, target_link_libraries, or set().",
        "threshold": 5,
        "patterns": [
            (r"^\s*cmake_minimum_required\s*\(", 6),
            (r"^\s*project\s*\(", 4),
            (r"^\s*add_(executable|library)\s*\(", 5),
            (r"^\s*target_link_libraries\s*\(", 4),
            (r"^\s*set\s*\(\s*\w+", 2),
        ],
    },
    {
        "language": "Makefile",
        "confidence": 92,
        "evidence": "Makefile syntax: targets with dependencies, tab-indented commands, .PHONY, variables with :=, or $(...) expansion.",
        "threshold": 6,
        "patterns": [
            (r"^\s*\.phony\s*:", 5),
            (r"^[\w./-]+\s*:\s*[\w./ -]*$", 4),
            (r"^\t[^\n]+", 4),
            (r"^\s*\w+\s*[:?+]?=\s*", 3),
            (r"\$\([^)]+\)", 2),
        ],
    },
    {
        "language": "HTML",
        "confidence": 94,
        "evidence": "HTML syntax: doctype/html/head/body tags, script/style tags, or semantic markup.",
        "threshold": 6,
        "patterns": [
            (r"<!doctype\s+html", 6),
            (r"<html\b", 5),
            (r"<(?:head|body|main|section|div|script|style)\b", 3),
            (r"</(?:html|body|div|script|style)>", 3),
            (r"\bclass\s*=\s*['\"]", 2),
        ],
    },
    {
        "language": "CSS",
        "confidence": 90,
        "evidence": "CSS syntax: selectors with declaration blocks, @media/@keyframes, CSS properties, or custom properties.",
        "threshold": 6,
        "patterns": [
            (r"@media\s+[^{]+\{", 4),
            (r"@keyframes\s+\w+", 4),
            (r"[.#]?\w[\w-]*\s*\{[^}]*\}", 4),
            (r"\b(display|position|color|background|font-size|margin|padding)\s*:", 3),
            (r"--[\w-]+\s*:", 2),
        ],
    },
    {
        "language": "JSON",
        "confidence": 90,
        "evidence": "JSON syntax: quoted object keys, arrays/objects, colon separators, and no executable code syntax.",
        "threshold": 7,
        "patterns": [
            (r"^\s*[\{\[]", 3),
            (r"\"[\w.-]+\"\s*:", 4),
            (r":\s*(?:\"[^\"]*\"|\d+|true|false|null|[\{\[])", 3),
            (r"[\}\]]\s*$", 2),
        ],
    },
    {
        "language": "XML",
        "confidence": 92,
        "evidence": "XML syntax: XML declaration, namespaced tags, matching closing tags, or self-closing elements.",
        "threshold": 6,
        "patterns": [
            (r"<\?xml\s+version=", 6),
            (r"<[\w:-]+(?:\s+[\w:-]+=\"[^\"]*\")+\s*/?>", 4),
            (r"</[\w:-]+>", 3),
            (r"\s+xmlns[:=]", 3),
        ],
    },
    {
        "language": "TOML",
        "confidence": 90,
        "evidence": "TOML syntax: [section] headers, key = value pairs, arrays of tables, or quoted scalar values.",
        "threshold": 6,
        "patterns": [
            (r"^\s*\[[\w.-]+\]\s*$", 4),
            (r"^\s*\[\[[\w.-]+\]\]\s*$", 5),
            (r"^\s*[\w.-]+\s*=\s*(?:\"[^\"]*\"|\d+|true|false|\[)", 3),
            (r"^\s*#\s*.+", 1),
        ],
    },
    {
        "language": "INI",
        "confidence": 84,
        "evidence": "INI syntax: [section] headers with key=value settings.",
        "threshold": 7,
        "patterns": [
            (r"^\s*\[[\w .-]+\]\s*$", 4),
            (r"^\s*[\w.-]+\s*=\s*[^=\n]+$", 3),
            (r"^\s*[;#]\s*.+", 1),
        ],
    },
    {
        "language": "GraphQL",
        "confidence": 90,
        "evidence": "GraphQL syntax: query/mutation/subscription blocks, type definitions, fragments, or schema fields.",
        "threshold": 6,
        "patterns": [
            (r"\b(query|mutation|subscription)\s+\w*\s*\{", 5),
            (r"^\s*type\s+\w+\s*\{", 5),
            (r"^\s*fragment\s+\w+\s+on\s+\w+", 4),
            (r"^\s*(schema|input|enum)\s+\w*\s*\{", 3),
        ],
    },
    {
        "language": "Protocol Buffers",
        "confidence": 94,
        "evidence": "Protocol Buffers syntax: syntax = proto2/proto3, message/service/rpc definitions, or numbered fields.",
        "threshold": 6,
        "patterns": [
            (r"^\s*syntax\s*=\s*\"proto[23]\"\s*;", 6),
            (r"^\s*message\s+\w+\s*\{", 5),
            (r"^\s*service\s+\w+\s*\{", 4),
            (r"\brpc\s+\w+\s*\(", 4),
            (r"=\s*\d+\s*;", 2),
        ],
    },
    {
        "language": "PL/SQL",
        "confidence": 90,
        "evidence": "PL/SQL syntax: CREATE OR REPLACE PROCEDURE/FUNCTION, DECLARE/BEGIN/END blocks, or Oracle exception handlers.",
        "threshold": 6,
        "patterns": [
            (r"create\s+or\s+replace\s+(procedure|function|package)\b", 6),
            (r"^\s*declare\s*$", 3),
            (r"^\s*begin\s*$", 2),
            (r"\bexception\s+when\s+others\s+then\b", 4),
            (r"%type\b|%rowtype\b", 3),
        ],
    },
    {
        "language": "T-SQL",
        "confidence": 88,
        "evidence": "T-SQL syntax: GO batch separators, CREATE PROCEDURE, @variables, SELECT TOP, or TRY/CATCH blocks.",
        "threshold": 6,
        "patterns": [
            (r"^\s*go\s*$", 4),
            (r"create\s+(procedure|proc)\b", 5),
            (r"@\w+\s+\w+", 3),
            (r"\bselect\s+top\s+\d+", 3),
            (r"\bbegin\s+try\b|\bbegin\s+catch\b", 4),
        ],
    },
    {
        "language": "Fortran",
        "confidence": 92,
        "evidence": "Fortran syntax: program/subroutine, implicit none, :: declarations, print *, or end program.",
        "threshold": 6,
        "patterns": [
            (r"^\s*program\s+\w+", 5),
            (r"^\s*implicit\s+none\b", 5),
            (r"^\s*(subroutine|function)\s+\w+", 4),
            (r"\b(real|integer|character|logical)\s*::", 3),
            (r"^\s*print\s*\*", 3),
            (r"^\s*end\s+program\b", 4),
        ],
    },
    {
        "language": "COBOL",
        "confidence": 94,
        "evidence": "COBOL syntax: IDENTIFICATION/PROCEDURE DIVISION, PROGRAM-ID, WORKING-STORAGE, or PERFORM statements.",
        "threshold": 6,
        "patterns": [
            (r"^\s*identification\s+division\.", 6),
            (r"^\s*program-id\.", 5),
            (r"^\s*data\s+division\.", 4),
            (r"^\s*working-storage\s+section\.", 4),
            (r"^\s*procedure\s+division\.", 5),
            (r"\bperform\s+\w+", 2),
        ],
    },
    {
        "language": "Pascal",
        "confidence": 90,
        "evidence": "Pascal/Delphi syntax: program/unit, uses, begin/end., var sections, procedure/function declarations.",
        "threshold": 6,
        "patterns": [
            (r"^\s*(program|unit)\s+\w+\s*;", 5),
            (r"^\s*uses\s+[\w, ]+;", 4),
            (r"^\s*var\s*$", 2),
            (r"^\s*(procedure|function)\s+\w+", 4),
            (r"^\s*begin\s*$", 2),
            (r"^\s*end\.\s*$", 4),
        ],
    },
    {
        "language": "Ada",
        "confidence": 90,
        "evidence": "Ada syntax: with/use Ada packages, procedure ... is, begin/end, Put_Line, or typed declarations.",
        "threshold": 6,
        "patterns": [
            (r"^\s*with\s+ada\.", 5),
            (r"^\s*use\s+ada\.", 4),
            (r"^\s*procedure\s+\w+\s+is\b", 5),
            (r"\bput_line\s*\(", 3),
            (r"^\s*begin\s*$", 2),
            (r"^\s*end\s+\w+\s*;", 3),
        ],
    },
    {
        "language": "Prolog",
        "confidence": 88,
        "evidence": "Prolog syntax: facts/rules ending with periods, :- rules/directives, variables with capital letters, or queries.",
        "threshold": 6,
        "patterns": [
            (r"^\s*:-\s*\w+", 4),
            (r"^\s*\w+\([^)]*\)\s*:-", 5),
            (r"^\s*\w+\([^)]*\)\s*\.", 3),
            (r"\b[A-Z]\w*\b", 1),
            (r"\bis\b|\bmember\s*\(", 2),
        ],
    },
    {
        "language": "Common Lisp",
        "confidence": 88,
        "evidence": "Common Lisp syntax: defun, defpackage, in-package, let forms, format t, or setq.",
        "threshold": 6,
        "patterns": [
            (r"\(defun\s+\w+", 5),
            (r"\(defpackage\s+[:\w-]+", 4),
            (r"\(in-package\s+[:\w-]+", 4),
            (r"\(let\s*\(", 3),
            (r"\(format\s+t\s+", 3),
            (r"\(setq\s+\w+", 2),
        ],
    },
    {
        "language": "Racket",
        "confidence": 92,
        "evidence": "Racket syntax: #lang racket, define forms, lambda, require, or module+.",
        "threshold": 5,
        "patterns": [
            (r"^\s*#lang\s+racket\b", 6),
            (r"\(define\s+\(", 4),
            (r"\(lambda\s*\(", 3),
            (r"\(require\s+[\w/.-]+", 3),
            (r"\(module\+\s+\w+", 3),
        ],
    },
    {
        "language": "Scheme",
        "confidence": 86,
        "evidence": "Scheme syntax: define/lambda forms, let forms, display, or Scheme imports.",
        "threshold": 6,
        "patterns": [
            (r"\(define\s+\w+", 4),
            (r"\(lambda\s*\(", 3),
            (r"\(let\s*\(", 3),
            (r"\(display\s+", 3),
            (r"\(import\s+\(", 2),
        ],
    },
    {
        "language": "MATLAB",
        "confidence": 90,
        "evidence": "MATLAB syntax: function ... = name(...), end, disp, matrix indexing, or % comments.",
        "threshold": 6,
        "patterns": [
            (r"^\s*function\s+(?:\[[^\]]+\]\s*=\s*|\w+\s*=\s*)?\w+\s*\(", 5),
            (r"^\s*end\s*$", 2),
            (r"\bdisp\s*\(", 3),
            (r"%\s*.+", 1),
            (r"\bzeros\s*\(|\bones\s*\(|\bsize\s*\(", 2),
        ],
    },
    {
        "language": "SAS",
        "confidence": 88,
        "evidence": "SAS syntax: DATA/PROC steps, SET statements, RUN;, LIBNAME, or DATALINES.",
        "threshold": 6,
        "patterns": [
            (r"^\s*data\s+\w+\s*;", 5),
            (r"^\s*proc\s+\w+", 5),
            (r"^\s*set\s+\w+\s*;", 3),
            (r"^\s*run\s*;", 3),
            (r"^\s*libname\s+\w+", 3),
            (r"^\s*datalines\s*;", 3),
        ],
    },
    {
        "language": "Verilog",
        "confidence": 90,
        "evidence": "Verilog syntax: module/endmodule, input/output/wire/reg, always blocks, or assign statements.",
        "threshold": 6,
        "patterns": [
            (r"^\s*module\s+\w+\s*\(", 5),
            (r"^\s*endmodule\b", 5),
            (r"\b(input|output|wire|reg)\b", 3),
            (r"\balways\s*@\s*\(", 4),
            (r"^\s*assign\s+\w+\s*=", 3),
        ],
    },
    {
        "language": "SystemVerilog",
        "confidence": 92,
        "evidence": "SystemVerilog syntax: logic, always_ff/always_comb, interface, class, or endmodule.",
        "threshold": 7,
        "patterns": [
            (r"^\s*module\s+\w+\s*[#(]", 4),
            (r"\blogic\s+(?:\[[^\]]+\]\s*)?\w+", 4),
            (r"\balways_(ff|comb|latch)\b", 5),
            (r"^\s*interface\s+\w+", 5),
            (r"^\s*class\s+\w+", 4),
            (r"^\s*endmodule\b", 3),
        ],
    },
    {
        "language": "VHDL",
        "confidence": 92,
        "evidence": "VHDL syntax: library/use IEEE, entity/architecture, std_logic, begin/end, or signal declarations.",
        "threshold": 6,
        "patterns": [
            (r"^\s*library\s+ieee\s*;", 5),
            (r"^\s*use\s+ieee\.", 4),
            (r"^\s*entity\s+\w+\s+is\b", 5),
            (r"^\s*architecture\s+\w+\s+of\s+\w+\s+is\b", 5),
            (r"\bstd_logic\b", 3),
            (r"^\s*signal\s+\w+\s*:", 3),
        ],
    },
    {
        "language": "Assembly",
        "confidence": 86,
        "evidence": "Assembly syntax: section .text, global _start/main, labels, registers, mov/call/int instructions.",
        "threshold": 6,
        "patterns": [
            (r"^\s*section\s+\.text\b", 5),
            (r"^\s*global\s+[_\w]+", 4),
            (r"^\s*[_a-z]\w*:\s*$", 3),
            (r"\b(mov|lea|push|pop|call|ret|jmp|cmp)\s+", 2),
            (r"\b(eax|ebx|ecx|edx|rax|rbx|rcx|rdx|rsp|rbp)\b", 2),
            (r"\bint\s+0x[0-9a-f]+", 3),
        ],
    },
    {
        "language": "Vue",
        "confidence": 92,
        "evidence": "Vue syntax: <template>, <script setup>, defineProps, createApp, or Vue single-file component sections.",
        "threshold": 6,
        "patterns": [
            (r"<template\b", 4),
            (r"<script\s+setup\b", 5),
            (r"\bdefineprops\s*<|\bdefineprops\s*\(", 4),
            (r"\bcreateapp\s*\(", 3),
            (r"<style\s+scoped\b", 3),
        ],
    },
    {
        "language": "Svelte",
        "confidence": 90,
        "evidence": "Svelte syntax: <script>, {#if}/{#each} blocks, on: event handlers, bind:, or svelte imports.",
        "threshold": 6,
        "patterns": [
            (r"<script\b", 2),
            (r"\{#(if|each|await)\b", 5),
            (r"\{/(if|each|await)\}", 4),
            (r"\bon:\w+=", 3),
            (r"\bbind:\w+=", 3),
            (r"from\s+['\"]svelte", 4),
        ],
    },
    {
        "language": "QML",
        "confidence": 90,
        "evidence": "QML syntax: import QtQuick, ApplicationWindow/Item, property declarations, signal handlers, or anchors.",
        "threshold": 6,
        "patterns": [
            (r"^\s*import\s+qtquick\b", 5),
            (r"\b(applicationwindow|item|rectangle)\s*\{", 4),
            (r"^\s*property\s+\w+\s+\w+", 4),
            (r"\bon\w+:\s*", 3),
            (r"\banchors\.", 3),
        ],
    },
    {
        "language": "Nix",
        "confidence": 88,
        "evidence": "Nix syntax: mkDerivation, flake inputs/outputs, let/in, with pkgs, or nixpkgs imports.",
        "threshold": 6,
        "patterns": [
            (r"\bstdenv\.mkderivation\s*\{", 5),
            (r"\binputs\s*=\s*\{", 3),
            (r"\boutputs\s*=\s*\{", 3),
            (r"\blet\s+.*\bin\b", 3),
            (r"\bwith\s+pkgs\s*;", 3),
            (r"<nixpkgs>", 4),
        ],
    },
    {
        "language": "GDScript",
        "confidence": 88,
        "evidence": "GDScript syntax: extends Node, func _ready, @export, var :=, signals, or Godot node paths.",
        "threshold": 6,
        "patterns": [
            (r"^\s*extends\s+\w+", 5),
            (r"^\s*func\s+_ready\s*\(", 5),
            (r"^\s*@export\b", 4),
            (r"\bvar\s+\w+\s*:=", 3),
            (r"^\s*signal\s+\w+", 3),
            (r"\$[\w/]+", 2),
        ],
    },
    {
        "language": "ColdFusion",
        "confidence": 88,
        "evidence": "ColdFusion/CFML syntax: cfcomponent/cffunction/cfquery tags or cfscript blocks.",
        "threshold": 6,
        "patterns": [
            (r"<cfcomponent\b", 5),
            (r"<cffunction\b", 5),
            (r"<cfquery\b", 5),
            (r"<cfscript\b", 4),
            (r"\bcfset\b", 3),
        ],
    },
    {
        "language": "ABAP",
        "confidence": 86,
        "evidence": "ABAP syntax: REPORT, DATA declarations, SELECT ... INTO, WRITE, FORM/ENDFORM, or LOOP AT.",
        "threshold": 6,
        "patterns": [
            (r"^\s*report\s+\w+\.", 5),
            (r"^\s*data\s*:\s*", 4),
            (r"\bselect\b.+\binto\b", 3),
            (r"^\s*write\s*:", 3),
            (r"^\s*form\s+\w+\.|^\s*endform\.", 3),
            (r"\bloop\s+at\b", 3),
        ],
    },
    {
        "language": "Apex",
        "confidence": 88,
        "evidence": "Salesforce Apex syntax: @AuraEnabled, SObject/Account, SOQL in brackets, with sharing, or Apex class methods.",
        "threshold": 6,
        "patterns": [
            (r"@auraenabled\b", 5),
            (r"\bwith\s+sharing\s+class\s+\w+", 5),
            (r"\b(?:sobject|account|contact|opportunity)\b", 3),
            (r"\[[\s\n]*select\s+.+\s+from\s+\w+", 4),
            (r"\btrigger\s+\w+\s+on\s+\w+", 5),
        ],
    },
    {
        "language": "Raku",
        "confidence": 84,
        "evidence": "Raku syntax: use v6, my $ variables with sigils, sub declarations, say, or MAIN.",
        "threshold": 6,
        "patterns": [
            (r"^\s*use\s+v6\b", 5),
            (r"^\s*sub\s+\w+\s*\(", 3),
            (r"\bmy\s+[$@%]\w+", 3),
            (r"\bsay\s+", 2),
            (r"\bmulti\s+sub\b", 4),
            (r"\bunit\s+module\b", 3),
        ],
    },
    {
        "language": "Tcl",
        "confidence": 84,
        "evidence": "Tcl syntax: proc definitions, puts, set variables, braces for command bodies, or package require.",
        "threshold": 6,
        "patterns": [
            (r"^\s*proc\s+\w+\s+\{[^}]*\}\s+\{", 5),
            (r"^\s*puts\s+", 3),
            (r"^\s*set\s+\w+\s+", 3),
            (r"^\s*package\s+require\s+\w+", 4),
            (r"\$\w+", 2),
        ],
    },
    {
        "language": "Forth",
        "confidence": 82,
        "evidence": "Forth syntax: colon definitions ending with semicolon, stack comments, DUP/SWAP/DROP/OVER words, or .\" output words.",
        "threshold": 6,
        "patterns": [
            (r"^\s*:\s+\w+\s+.+;\s*$", 6),
            (r"\(\s*--\s*[^)]*\)", 3),
            (r"\b(dup|swap|drop|over|rot)\b", 3),
            (r"\bemit\b|\bcr\b", 2),
            (r"\.\s+\"", 3),
        ],
    },
    {
        "language": "Elm",
        "confidence": 86,
        "evidence": "Elm syntax: module ... exposing, import Html, main/view/update functions, or type alias.",
        "threshold": 6,
        "patterns": [
            (r"^\s*module\s+\w+\s+exposing\s*\(", 5),
            (r"^\s*import\s+html\b", 4),
            (r"^\s*type\s+alias\s+\w+", 4),
            (r"^\s*main\s*=", 3),
            (r"^\s*view\s+\w+\s*=", 3),
        ],
    },
    {
        "language": "Dart",
        "confidence": 95,
        "evidence": "Dart/Flutter syntax: package:flutter imports, runApp, Widget build, StatelessWidget/StatefulWidget, or Future<void> main.",
        "threshold": 5,
        "patterns": [
            (r"import\s+['\"]package:flutter/", 5),
            (r"\brunapp\s*\(", 4),
            (r"\b(statelesswidget|statefulwidget|widget\s+build)\b", 4),
            (r"\bfuture\s*<\s*void\s*>\s+main\s*\(", 3),
            (r"\bvoid\s+main\s*\(\s*\)\s*(?:async\s*)?\{", 2),
        ],
    },
    {
        "language": "Scala",
        "confidence": 94,
        "evidence": "Scala syntax: object extends App, case class, val/var, def, or scala imports.",
        "threshold": 5,
        "patterns": [
            (r"\bobject\s+\w+\s+extends\s+app\b", 5),
            (r"\bcase\s+class\s+\w+", 4),
            (r"\bimport\s+scala\.", 4),
            (r"\bdef\s+\w+\s*\([^)]*\)\s*[:=]", 3),
            (r"\bval\s+\w+\s*=", 2),
        ],
    },
    {
        "language": "Haskell",
        "confidence": 94,
        "evidence": "Haskell syntax: module ... where, type signatures with ::, main :: IO, do blocks, or <- binding.",
        "threshold": 5,
        "patterns": [
            (r"^\s*module\s+[\w.]+\s+where\b", 5),
            (r"^\s*main\s*::\s*io\s*\(\s*\)", 5),
            (r"^\s*\w+\s*::\s*[^=\n]+", 3),
            (r"\bimport\s+data\.", 3),
            (r"<-\s*\w+", 2),
        ],
    },
    {
        "language": "Erlang",
        "confidence": 95,
        "evidence": "Erlang syntax: -module(...), -export([...]), function clauses with ->, and atoms ending clauses with periods.",
        "threshold": 5,
        "patterns": [
            (r"^\s*-module\s*\([^)]+\)\s*\.", 5),
            (r"^\s*-export\s*\(\s*\[", 5),
            (r"\w+\s*\([^)]*\)\s*->", 3),
            (r"\breceive\b|\bspawn\s*\(", 3),
            (r"\.\s*$", 1),
        ],
    },
    {
        "language": "F#",
        "confidence": 92,
        "evidence": "F# syntax: open System, [<EntryPoint>], let bindings, printfn, or module declarations.",
        "threshold": 5,
        "patterns": [
            (r"^\s*open\s+system\b", 4),
            (r"\[<entrypoint>\]", 5),
            (r"^\s*module\s+[\w.]+", 3),
            (r"^\s*let\s+\w+\s+[^=]*=", 3),
            (r"\bprintfn\s+\"", 3),
        ],
    },
    {
        "language": "Objective-C",
        "confidence": 95,
        "evidence": "Objective-C syntax: #import Foundation/UIKit, @interface, @implementation, NSLog, or NSString.",
        "threshold": 5,
        "patterns": [
            (r"#import\s+<(?:foundation|uikit)/", 5),
            (r"@interface\s+\w+", 5),
            (r"@implementation\s+\w+", 5),
            (r"\bnslog\s*\(", 3),
            (r"\bnsstring\s*\*", 3),
        ],
    },
    {
        "language": "VB.NET",
        "confidence": 94,
        "evidence": "VB.NET syntax: Imports System, Module/Class, Sub Main, Dim, and End Sub/End Module.",
        "threshold": 5,
        "patterns": [
            (r"^\s*imports\s+system\b", 4),
            (r"^\s*(module|class)\s+\w+", 3),
            (r"^\s*sub\s+main\s*\(", 4),
            (r"^\s*dim\s+\w+\s+as\s+\w+", 3),
            (r"^\s*end\s+(sub|module|class)\b", 3),
        ],
    },
    {
        "language": "R",
        "confidence": 90,
        "evidence": "R syntax: library(...), <- assignments, function definitions, data.frame/tibble/dplyr usage.",
        "threshold": 5,
        "patterns": [
            (r"^\s*library\s*\(", 4),
            (r"<-\s*function\s*\(", 4),
            (r"\bdata\.frame\s*\(", 3),
            (r"\btibble\s*\(|\bdplyr::|\bggplot\s*\(", 3),
            (r"^\s*\w+\s*<-\s*", 2),
        ],
    },
    {
        "language": "Julia",
        "confidence": 92,
        "evidence": "Julia syntax: using/import packages, function ... end, println, DataFrame, or module ... end.",
        "threshold": 5,
        "patterns": [
            (r"^\s*using\s+[\w., ]+", 4),
            (r"^\s*function\s+\w+\s*\([^)]*\)", 4),
            (r"\bdataframe\s*\(", 3),
            (r"\bprintln\s*\(", 2),
            (r"^\s*end\s*$", 1),
        ],
    },
    {
        "language": "Solidity",
        "confidence": 98,
        "evidence": "Solidity syntax: pragma solidity, contract, mapping, address, msg.sender, or external/public functions.",
        "threshold": 5,
        "patterns": [
            (r"pragma\s+solidity\b", 6),
            (r"\bcontract\s+\w+\s*\{", 5),
            (r"\bmapping\s*\(", 3),
            (r"\bmsg\.sender\b", 3),
            (r"\bfunction\s+\w+\s*\([^)]*\)\s*(public|external|internal|private)\b", 3),
        ],
    },
    {
        "language": "OCaml",
        "confidence": 98,
        "evidence": "OCaml/Opium syntax: open Lwt/Opium, let bindings, |> pipelines, >>= Lwt binds, variant matches, or App.post/App.get routes.",
        "threshold": 5,
        "patterns": [
            (r"^\s*open\s+(?:lwt|opium|yojson|cohttp|sqlite3)\b", 5),
            (r"\bapp\.(?:post|get|put|delete)\s+\"[^\"]+\"", 5),
            (r"\|\>\s*\w+", 3),
            (r">>=\s*fun\b", 4),
            (r"\bmatch\s+.+\s+with\b", 3),
            (r"^\s*let\s+\w+[^=]*=", 2),
            (r"`assoc\s*\[", 3),
        ],
    },
    {
        "language": "Groovy",
        "confidence": 98,
        "evidence": "Groovy syntax: groovy.json/groovy.sql imports, def variables, static methods, GString interpolation, map literals, or command.execute().",
        "threshold": 5,
        "patterns": [
            (r"^\s*import\s+groovy\.", 6),
            (r"^\s*import\s+groovy\.sql\.sql\b", 6),
            (r"\bclass\s+\w+\s*\{", 2),
            (r"\bstatic\s+(?:map|string|sql)\s+\w+\s*\(", 4),
            (r"\bdef\s+\w+\s*=", 3),
            (r"\$\{[^}]+\}", 3),
            (r"\bnew\s+groovyshell\s*\(", 5),
            (r"\.execute\s*\(\s*\)", 4),
        ],
    },
    {
        "language": "D",
        "confidence": 98,
        "evidence": "D/vibe.d syntax: import vibe.d/std.*, enum constants, HTTPServerRequest/HTTPServerResponse, URLRouter, listenHTTP, runApplication, or ~ string concatenation.",
        "threshold": 5,
        "patterns": [
            (r"^\s*import\s+vibe\.d\s*;", 6),
            (r"^\s*import\s+std\.[\w.]+\s*;", 3),
            (r"\benum\s+\w+\s*=", 3),
            (r"\bvoid\s+\w+\s*\(\s*httpserverrequest\s+\w+\s*,\s*httpserverresponse\s+\w+\s*\)", 6),
            (r"\bnew\s+urlrouter\b", 5),
            (r"\blistenhttp\s*\(", 5),
            (r"\brunapplication\s*\(", 5),
            (r"~\s*\w+", 2),
        ],
    },
    {
        "language": "Smalltalk",
        "confidence": 98,
        "evidence": "Smalltalk syntax: Object subclass:, class >> methods, := assignment, ^ returns, | local variables |, Dictionary new cascades, or message keywords ending with colon.",
        "threshold": 5,
        "patterns": [
            (r"\bobject\s+subclass:\s*#\w+", 6),
            (r"\b\w+\s+class\s*>>\s*\w+", 6),
            (r"^\s*\|\s*[\w\s]+\s*\|", 3),
            (r":=\s*", 2),
            (r"^\s*\^\s+", 2),
            (r"\bdictionary\s+new\b", 3),
            (r"\byourself\b", 2),
            (r"\bfilestream\b|\bosprocess\s+command:|\bznclient\s+new\b", 4),
        ],
    },
    {
        "language": "Zig",
        "confidence": 98,
        "evidence": "Zig syntax: @import(\"std\"), pub fn/fn with ! error unions, []const u8 slices, std.mem.Allocator, try, and .{ } literals.",
        "threshold": 5,
        "patterns": [
            (r"@import\s*\(\s*\"std\"\s*\)", 6),
            (r"\bpub\s+fn\s+\w+\s*\([^)]*\)\s*!?\s*\w*", 4),
            (r"\bfn\s+\w+\s*\([^)]*\)\s*!\s*\w+", 4),
            (r"\[\]\s*const\s+u8", 4),
            (r"\bstd\.mem\.allocator\b", 4),
            (r"\btry\s+std\.", 3),
            (r"\.\{\s*[^}]*\}", 2),
            (r"\bstd\.process\.child\.run\b", 4),
        ],
    },
    {
        "language": "Terraform",
        "confidence": 96,
        "evidence": "Terraform/HCL syntax: resource/provider/variable blocks and var.* references.",
        "threshold": 5,
        "patterns": [
            (r"^\s*resource\s+\"[^\"]+\"\s+\"[^\"]+\"\s*\{", 6),
            (r"^\s*provider\s+\"[^\"]+\"\s*\{", 5),
            (r"^\s*variable\s+\"[^\"]+\"\s*\{", 4),
            (r"\bvar\.\w+\b", 3),
            (r"^\s*terraform\s*\{", 3),
        ],
    },
    {
        "language": "Dockerfile",
        "confidence": 96,
        "evidence": "Dockerfile syntax: FROM, RUN, COPY, EXPOSE, CMD, or ENTRYPOINT instructions.",
        "threshold": 5,
        "patterns": [
            (r"^\s*from\s+[\w./:-]+", 5),
            (r"^\s*run\s+.+", 2),
            (r"^\s*copy\s+.+", 2),
            (r"^\s*expose\s+\d+", 2),
            (r"^\s*(cmd|entrypoint)\s+", 2),
        ],
    },
    {
        "language": "YAML",
        "confidence": 88,
        "evidence": "YAML syntax: key/value indentation, lists, apiVersion/kind, services, or GitHub Actions workflow keys.",
        "threshold": 6,
        "patterns": [
            (r"^\s*apiversion\s*:", 4),
            (r"^\s*kind\s*:", 3),
            (r"^\s*services\s*:", 4),
            (r"^\s*on\s*:\s*$", 3),
            (r"^\s*-\s+\w+:", 3),
            (r"^\s+\w[\w-]*\s*:", 2),
        ],
    },
]


def _cache_limit() -> int:
    return _int_env("REVIEW_CACHE_MAX_ENTRIES", 50, 1, 500)


def _similarity_threshold() -> float:
    try:
        value = float(os.getenv("REVIEW_SIMILARITY_THRESHOLD", "0.9"))
    except ValueError:
        value = 0.9
    return max(0.5, min(value, 0.99))


def _safe_json_loads(text: str) -> Dict[str, Any]:
    """Parse a JSON object from an AI response, including markdown-wrapped output."""
    cleaned = text.strip()

    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        parsed = json.loads(cleaned[start : end + 1])

    if not isinstance(parsed, dict):
        raise ValueError("AI response must be a JSON object.")

    return parsed


def _coerce_risk_score(value: Any) -> int:
    if isinstance(value, (int, float)):
        score = int(value)
    elif isinstance(value, str):
        match = re.search(r"\d+", value)
        score = int(match.group(0)) if match else 50
    else:
        score = 50

    return max(0, min(score, 100))


def _string_list(value: Any, fallback: list[str]) -> list[str]:
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
        return _dedupe_strings(items) or fallback
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return fallback


def _dedupe_strings(items: list[str]) -> list[str]:
    seen: set[str] = set()
    unique_items: list[str] = []
    for item in items:
        normalized = re.sub(r"\s+", " ", item.strip()).lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique_items.append(item.strip())
    return unique_items


def _has_probable_division(code: str) -> bool:
    code_without_strings = re.sub(r"(['\"])(?:\\.|(?!\1).)*\1", "\"\"", code)
    return bool(re.search(r"[\w)\]]+\s*/\s*[\w(\[]+", code_without_strings))


def _is_ollama_base_url(base_url: str) -> bool:
    normalized = base_url.lower()
    return "ollama" in normalized or "localhost:11434" in normalized or "127.0.0.1:11434" in normalized


def _is_javascript_language(language: str | None) -> bool:
    if not language:
        return False
    return language.strip().lower() in _JAVASCRIPT_LANGUAGES


def _is_auto_language(language: str | None) -> bool:
    if not language:
        return True
    return language.strip().lower() in {"auto", "auto-detect", "autodetect"}


def _code_looks_like_ruby(code_lower: str) -> bool:
    if _code_looks_like_crystal(code_lower):
        return False
    return bool(
        re.search(r"^\s*require\s+['\"](?:sinatra|sqlite3|json|yaml|net/http|uri|fileutils)['\"]", code_lower, re.MULTILINE)
        or re.search(r"^\s*(get|post|put|patch|delete)\s+['\"][^'\"]+['\"]\s+do\b", code_lower, re.MULTILINE)
        or "sqlite3::database" in code_lower
        or "net::http" in code_lower
        or "fileutils" in code_lower
        or "yaml.load" in code_lower
    )


def _code_looks_like_crystal(code_lower: str) -> bool:
    return bool(
        re.search(r"^\s*require\s+['\"](?:kemal|http/client|file_utils)['\"]", code_lower, re.MULTILINE)
        or re.search(r"\bdo\s+\|env\|", code_lower)
        or re.search(r"\.as_[sifb]\b", code_lower)
        or "kemal.run" in code_lower
        or "http::client" in code_lower
        or "fileutils.mkdir_p" in code_lower
        or "time.utc" in code_lower
    )


def _code_looks_like_elixir(code_lower: str) -> bool:
    return bool(
        re.search(r"^\s*defmodule\s+\w+", code_lower, re.MULTILINE)
        or re.search(r"^\s*@\w+\s+", code_lower, re.MULTILINE)
        and " do\n" in code_lower
        or "postgrex." in code_lower
        or "jason.encode!" in code_lower
        or "system.cmd" in code_lower
        or "datetime.utc_now()" in code_lower
    )


def _code_looks_like_nim(code_lower: str) -> bool:
    return bool(
        re.search(r"^\s*import\s+(?:jester|db_sqlite|osproc|strformat|strutils|httpclient)\b", code_lower, re.MULTILINE)
        or re.search(r"^\s*proc\s+\w+\s*\([^)]*\)\s*:", code_lower, re.MULTILINE)
        or re.search(r"^\s*routes\s*:", code_lower, re.MULTILINE)
        or re.search(r"^\s*when\s+ismainmodule\s*:", code_lower, re.MULTILINE)
        or "runforever()" in code_lower
        or "execmd(" in code_lower
        or "newhttpclient()" in code_lower
        or "db.getrow(sql(" in code_lower
    )


def _code_looks_like_clojure(code_lower: str) -> bool:
    return bool(
        re.search(r"^\s*\(ns\s+[\w.-]+", code_lower, re.MULTILINE)
        or re.search(r"^\s*\(defn\s+[\w!?\-]+", code_lower, re.MULTILINE)
        or re.search(r"^\s*\(defroutes\s+\w+", code_lower, re.MULTILINE)
        or re.search(r":require\s+\[", code_lower)
        or "clojure.java.jdbc" in code_lower
        or "ring.adapter.jetty" in code_lower
        or "compojure.core" in code_lower
        or "jdbc/query" in code_lower
        or "jdbc/execute!" in code_lower
        or "run-jetty" in code_lower
        or "wrap-json-body" in code_lower
    )


def _code_looks_like_lua(code_lower: str) -> bool:
    return bool(
        re.search(r"^\s*local\s+\w+\s*=\s*require\s+['\"](?:cjson|lsqlite3|resty\.http)['\"]", code_lower, re.MULTILINE)
        or re.search(r"^\s*local\s+function\s+\w+\s*\(", code_lower, re.MULTILINE)
        or (
            re.search(r"^\s*local\s+\w+\s*=", code_lower, re.MULTILINE)
            and ("ngx." in code_lower or "cjson." in code_lower or "sqlite3." in code_lower)
        )
        or "ngx.req." in code_lower
        or "ngx.var." in code_lower
        or "ngx.say" in code_lower
        or "resty.http" in code_lower
        or "lsqlite3" in code_lower
        or "cjson.decode" in code_lower
        or "cjson.encode" in code_lower
        or (
            re.search(r"^\s*if\s+.+\s+then\s*$", code_lower, re.MULTILINE)
            and re.search(r"^\s*end\s*$", code_lower, re.MULTILINE)
        )
    )


def _code_looks_like_perl(code_lower: str) -> bool:
    return bool(
        re.search(
            r"^\s*use\s+(?:strict|warnings|mojolicious::lite|dbi|json|yaml::xs|lwp::useragent|file::path|file::spec|posix)\b",
            code_lower,
            re.MULTILINE,
        )
        or re.search(r"^\s*my\s+\$\w+\s*=", code_lower, re.MULTILINE)
        or re.search(r"^\s*sub\s+\w+\s*\{", code_lower, re.MULTILINE)
        or re.search(r"^\s*(get|post|put|patch|delete)\s+['\"][^'\"]+['\"]\s*=>\s*sub\b", code_lower, re.MULTILINE)
        or "dbi->connect" in code_lower
        or "$c->render" in code_lower
        or "app->start" in code_lower
        or "lwp::useragent" in code_lower
    )


def _code_looks_like_node_js(code_lower: str) -> bool:
    if (
        _code_looks_like_ruby(code_lower)
        or _code_looks_like_ocaml(code_lower)
        or _code_looks_like_d(code_lower)
        or _code_looks_like_zig(code_lower)
        or _code_looks_like_pike(code_lower)
    ):
        return False
    return any(marker in code_lower for marker in _NODE_MARKERS) or bool(
        re.search(r"\b(require|import)\s*\(?\s*['\"](?:express|axios|fs|jsonwebtoken|child_process)['\"]", code_lower)
    )


def _code_looks_like_ocaml(code_lower: str) -> bool:
    return bool(
        re.search(r"^\s*open\s+(?:lwt|opium|yojson|cohttp|sqlite3)\b", code_lower, re.MULTILINE)
        or re.search(r"\bapp\.(?:post|get|put|delete)\s+\"[^\"]+\"", code_lower)
        or "lwt.infix" in code_lower
        or "yojson.safe" in code_lower
        or "cohttp_lwt_unix" in code_lower
        or "opium" in code_lower and re.search(r"^\s*let\s+\w+[^=]*=", code_lower, re.MULTILINE)
        or ">>= fun" in code_lower
        or "`assoc" in code_lower
    )


def _code_looks_like_groovy(code_lower: str) -> bool:
    return bool(
        re.search(r"^\s*import\s+groovy\.", code_lower, re.MULTILINE)
        or "groovy.sql.sql" in code_lower
        or "groovy.json." in code_lower
        or re.search(r"\bstatic\s+(?:map|string|sql)\s+\w+\s*\(", code_lower)
        or re.search(r"^\s*def\s+\w+\s*=", code_lower, re.MULTILINE)
        and ("${" in code_lower or ".execute()" in code_lower or "new file(" in code_lower)
        or "new groovyshell(" in code_lower
    )


def _code_looks_like_d(code_lower: str) -> bool:
    return bool(
        re.search(r"^\s*import\s+vibe\.d\s*;", code_lower, re.MULTILINE)
        or (
            re.search(r"^\s*import\s+std\.[\w.]+\s*;", code_lower, re.MULTILINE)
            and ("httpserverrequest" in code_lower or "urlrouter" in code_lower or "listenhttp(" in code_lower)
        )
        or ("httpserverrequest" in code_lower and "httpserverresponse" in code_lower)
        or "new urlrouter" in code_lower
        or "listenhttp(" in code_lower
        or "runapplication(" in code_lower
    )


def _code_looks_like_smalltalk(code_lower: str) -> bool:
    return bool(
        re.search(r"\bobject\s+subclass:\s*#\w+", code_lower)
        or re.search(r"\b\w+\s+class\s*>>\s*\w+", code_lower)
        or (
            ":=" in code_lower
            and "^" in code_lower
            and ("dictionary new" in code_lower or "yourself" in code_lower or "filestream" in code_lower)
        )
        or "osprocess command:" in code_lower
        or "znclient new" in code_lower
        or "compiler evaluate:" in code_lower
    )


def _code_looks_like_zig(code_lower: str) -> bool:
    return bool(
        '@import("std")' in code_lower
        or "@import('std')" in code_lower
        or re.search(r"\[\]\s*const\s+u8", code_lower)
        or "std.mem.allocator" in code_lower
        or "std.process.child.run" in code_lower
        or "std.fmt.allocprint" in code_lower
        or re.search(r"\bpub\s+fn\s+\w+\s*\([^)]*\)\s*!?\s*\w*", code_lower)
    )


def _code_looks_like_pike(code_lower: str) -> bool:
    return bool(
        re.search(r"^\s*import\s+(?:stdio|sql|standards\.json|protocols\.http)\s*;", code_lower, re.MULTILINE)
        or (
            re.search(r"^\s*constant\s+\w+\s*=", code_lower, re.MULTILINE)
            and re.search(r"\b(mapping|array|object|string|float|int)\s+\w+\s*(?:\(|=)", code_lower)
        )
        or "process.create_process" in code_lower
        or "standards.json." in code_lower
        or "protocols.http." in code_lower
        or (re.search(r"\(\[\s*\"[^\"]+\"\s*:", code_lower) and "->" in code_lower)
    )


def _code_looks_executable_not_data(code_lower: str) -> bool:
    return bool(
        re.search(
            r"^\s*(import|package|namespace|class|def|defn|fn|func|function|proc|sub|module|using|use|require|constant|mapping|array|object|string|float|int|void|public|private|protected|open|let|local|enum|struct)\b",
            code_lower,
            re.MULTILINE,
        )
        or re.search(r"\b\w+\s+\w+\s*\([^)]*\)\s*\{", code_lower)
        or re.search(r"->\s*\w+\s*\(", code_lower)
        or re.search(r";\s*$", code_lower, re.MULTILINE)
        or "object subclass:" in code_lower
        or "defmodule " in code_lower
    )


def _signature_language_detection(code_lower: str) -> tuple[str | None, int | None, str | None]:
    best_language: str | None = None
    best_confidence: int | None = None
    best_evidence: str | None = None
    best_score = 0

    for profile in _LANGUAGE_SIGNATURES:
        score = 0
        for pattern, weight in profile["patterns"]:
            if re.search(pattern, code_lower, re.MULTILINE):
                score += weight

        if score >= profile["threshold"] and score > best_score:
            if profile["language"] == "JSON" and _code_looks_executable_not_data(code_lower):
                continue
            best_language = str(profile["language"])
            best_confidence = int(profile["confidence"])
            best_evidence = str(profile["evidence"])
            best_score = score

    return best_language, best_confidence, best_evidence


def _syntax_language_detection(code: str) -> tuple[str | None, int | None, str | None]:
    code_lower = code.lower()

    if re.search(r"^\s*#{1,6}\s+\w+", code_lower, re.MULTILINE) and (
        "```" in code_lower
        or re.search(r"\[[^\]]+\]\([^)]+\)", code_lower)
        or re.search(r"^\s*[-*]\s+\[[ x]\]\s+", code_lower, re.MULTILINE)
    ):
        return "Markdown", 86, "Markdown syntax: headings, fenced code blocks, links, task lists, or list markup."

    strong_checks: list[tuple[str, int, str, bool]] = [
        ("Crystal", 98, 'Crystal/Kemal syntax: require "kemal", do |env|, .as_s/.as_f, or Kemal.run.', _code_looks_like_crystal(code_lower)),
        ("Perl", 98, "Perl/Mojolicious syntax: use strict, my $variable, sub, DBI->connect, $c->render, or app->start.", _code_looks_like_perl(code_lower)),
        ("Elixir", 98, "Elixir syntax: defmodule, @module attributes, Postgrex, Jason, or DateTime.utc_now().", _code_looks_like_elixir(code_lower)),
        ("Nim", 98, "Nim/Jester syntax: import jester, proc declarations, routes:, when isMainModule, or runForever().", _code_looks_like_nim(code_lower)),
        ("Clojure", 98, "Clojure/Ring syntax: (ns ...), (defn ...), defroutes, :require vectors, clojure.java.jdbc, or run-jetty.", _code_looks_like_clojure(code_lower)),
        ("Lua", 98, 'Lua/OpenResty syntax: local function/local variables, require "resty.http"/"cjson"/"lsqlite3", ngx.req, ngx.var, or cjson.encode.', _code_looks_like_lua(code_lower)),
        ("OCaml", 98, "OCaml/Opium syntax: open Lwt/Opium, let bindings, |> pipelines, >>= Lwt binds, variant matches, or App.post/App.get routes.", _code_looks_like_ocaml(code_lower)),
        ("Groovy", 98, "Groovy syntax: groovy.json/groovy.sql imports, def variables, static methods, GString interpolation, map literals, or command.execute().", _code_looks_like_groovy(code_lower)),
        ("D", 98, "D/vibe.d syntax: import vibe.d/std.*, enum constants, HTTPServerRequest/HTTPServerResponse, URLRouter, listenHTTP, runApplication, or ~ string concatenation.", _code_looks_like_d(code_lower)),
        ("Smalltalk", 98, "Smalltalk syntax: Object subclass:, class >> methods, := assignment, ^ returns, | local variables |, Dictionary new cascades, or message keywords ending with colon.", _code_looks_like_smalltalk(code_lower)),
        ("Zig", 98, 'Zig syntax: @import("std"), pub fn/fn with ! error unions, []const u8 slices, std.mem.Allocator, try, and .{ } literals.', _code_looks_like_zig(code_lower)),
        ("Pike", 98, "Pike syntax: import Stdio/Sql/Standards.JSON/Protocols.HTTP, constant declarations, mapping/array/object types, ([ ... ]) mappings, -> method calls, or Process.create_process.", _code_looks_like_pike(code_lower)),
        ("Ruby", 96, "Ruby/Sinatra syntax: require 'sinatra', route blocks with do/end, SQLite3::Database, or Net::HTTP.", _code_looks_like_ruby(code_lower)),
    ]

    for language, confidence, evidence, matched in strong_checks:
        if matched:
            return language, confidence, evidence

    signature_language, signature_confidence, signature_evidence = _signature_language_detection(code_lower)
    if signature_language:
        return signature_language, signature_confidence, signature_evidence

    if _code_looks_like_node_js(code_lower):
        return "JavaScript", 96, "JavaScript/Node.js syntax: require/import with Express, axios, fs, child_process, app.get/app.post, or module.exports."
    if re.search(r"\binterface\s+\w+|\btype\s+\w+\s*=|:\s*(string|number|boolean)\b|import\s+type\b", code_lower):
        return "TypeScript", 92, "TypeScript syntax: interfaces, type aliases, typed parameters, or import type."
    if re.search(r"#include\s*<|std::|\bnullptr\b|\bint\s+main\s*\(", code_lower):
        return "C++", 93, "C++ syntax: #include, std::, nullptr, or int main()."
    if re.search(r"\b(public|private|protected)\s+class\b|\bsystem\.out\.println\b|\bpublic\s+static\s+void\s+main\b", code_lower):
        return "Java", 92, "Java syntax: public/private class, System.out.println, or public static void main."
    if re.search(r"\busing\s+system\b|\bnamespace\s+\w+|\bconsole\.writeline\b", code_lower):
        return "C#", 92, "C# syntax: using System, namespace, or Console.WriteLine."
    if re.search(r"^\s*def\s+\w+\s*\(|^\s*import\s+\w+|^\s*from\s+\w+\s+import", code_lower, re.MULTILINE):
        return "Python", 92, "Python syntax: def, import, or from ... import."
    if re.search(r"^\s*package\s+main\b|\bfunc\s+\w+\s*\(|\bfmt\.println\b|\bgo\s+func\b", code_lower, re.MULTILINE):
        return "Go", 92, "Go syntax: package main, func declarations, fmt.Println, or goroutines."
    if re.search(r"\bfn\s+main\s*\(|\blet\s+mut\b|\bprintln!\s*\(|\bmatch\s+\w+\s*\{", code_lower):
        return "Rust", 92, "Rust syntax: fn main, let mut, println!, or match blocks."
    if re.search(r"<\?php|\buse\s+\w+(?:\\\w+)+;|\bfunction\s+\w+\s*\([^)]*\)\s*\{.*\$\w+", code_lower, re.DOTALL):
        return "PHP", 92, "PHP syntax: <?php, namespaced use statements, function declarations, or $variables."
    if re.search(r"\bfun\s+main\s*\(|\bdata\s+class\b|\bval\s+\w+|\bvar\s+\w+|\bprintln\s*\(", code_lower):
        return "Kotlin", 88, "Kotlin syntax: fun main, data class, val/var declarations, or println."
    if re.search(r"\bimport\s+swiftui\b|\bfunc\s+\w+\s*\(|\blet\s+\w+\s*=|\bvar\s+\w+\s*=", code_lower):
        return "Swift", 86, "Swift syntax: import SwiftUI, func declarations, let, or var."
    if re.search(r"^\s*#!/(?:usr/bin/env\s+)?(?:bash|sh)\b|^\s*(echo|grep|awk|sed|curl)\b|\$\{?\w+\}?", code_lower, re.MULTILINE):
        return "Bash", 82, "Shell syntax: bash/shebang, shell commands, or environment variable expansion."
    if re.search(r"\b(select|insert|update|delete)\b", code_lower) and not re.search(
        r"^\s*(?:\(|)(defn?|ns|defroutes|function|sub|proc|class|import|require|use|package|public|private|post|get)\b",
        code_lower,
        re.MULTILINE,
    ):
        return "SQL", 85, "Standalone SQL keywords without surrounding application-language syntax."

    return None, None, None


def _selected_language_matches_code(language: str | None, code_lower: str) -> bool:
    if _is_javascript_language(language) and _code_looks_like_node_js(code_lower):
        return True
    return False


def _detected_review_language(selected_language: str, code: str) -> str:
    code_lower = code.lower()
    selected = selected_language.strip() or "Plain Text"
    normalized_selected = selected.lower()

    syntax_language, _, _ = _syntax_language_detection(code)
    if syntax_language:
        return syntax_language
    if _code_looks_like_crystal(code_lower):
        return "Crystal"
    if _code_looks_like_ruby(code_lower):
        return "Ruby"
    if _code_looks_like_elixir(code_lower):
        return "Elixir"
    if _code_looks_like_nim(code_lower):
        return "Nim"
    if _code_looks_like_clojure(code_lower):
        return "Clojure"
    if _code_looks_like_lua(code_lower):
        return "Lua"
    if _code_looks_like_perl(code_lower):
        return "Perl"
    if _code_looks_like_node_js(code_lower):
        return "JavaScript"
    if re.search(r"\b(public|private|protected)\s+class\b|\bsystem\.out\.println\b|\bstring\[\]\s+args\b|\.getbytes\s*\(", code_lower):
        return "Java"
    if re.search(r"#include\s*<|std::|\bnullptr\b", code_lower):
        return "C++"
    if re.search(r"\binterface\s+\w+|\btype\s+\w+\s*=|:\s*(string|number|boolean)\b", code_lower) and normalized_selected in {"typescript", "ts", "react"}:
        return "TypeScript"
    if re.search(r"\b(select|insert|update|delete)\b", code_lower) and "def " not in code_lower and "function " not in code_lower:
        return "SQL"
    if re.search(r"^\s*def\s+\w+\s*\(|^\s*import\s+\w+|^\s*from\s+\w+\s+import", code_lower, re.MULTILINE):
        return "Python"
    if _is_javascript_language(selected):
        return "JavaScript"
    return selected


def _redact_for_ai_retry(code: str) -> str:
    redacted = re.sub(r"rm\s+-rf\s+[^\"'\n;)]+", "[dangerous shell command omitted]", code, flags=re.IGNORECASE)
    redacted = re.sub(r"(?i)(password|api[_-]?key|secret|token|admin[_-]?key)\s*=\s*([\"']).*?\2", r"\1 = \"[redacted-demo-secret]\"", redacted)
    return redacted


def _safe_exception_summary(exc: Exception) -> str:
    message = str(exc) or exc.__class__.__name__
    message = re.sub(r"sk-[A-Za-z0-9_\-]+", "[redacted-api-key]", message)
    message = re.sub(r"(?i)(api[_-]?key|authorization|bearer)\s*[:=]\s*\S+", r"\1=[redacted]", message)
    message = re.sub(r"\s+", " ", message).strip()
    if len(message) > 300:
        message = message[:297].rstrip() + "..."
    return message


def _should_skip_ai_retry(exc: Exception) -> bool:
    error_type = exc.__class__.__name__.lower()
    message = str(exc).lower()
    return any(
        marker in error_type or marker in message
        for marker in ["ratelimit", "rate limit", "429", "quota", "insufficient_quota", "billing"]
    )


def _int_env(name: str, default: int, min_value: int, max_value: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(min_value, min(value, max_value))


def _cache_key(payload: ReviewRequest) -> str:
    fingerprint = "|".join(
        [
            payload.language.strip().lower(),
            payload.focus.strip().lower(),
            hashlib.sha256(payload.code.encode("utf-8")).hexdigest(),
        ]
    )
    return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()


def _normalize_code_for_similarity(code: str) -> str:
    return re.sub(r"\s+", " ", code.strip())


def _code_sample_for_language_detection(code: str, max_lines: int = 120, max_chars: int = 6000) -> str:
    lines = code.splitlines()
    if len(lines) <= max_lines and len(code) <= max_chars:
        return code

    head_count = max_lines // 2
    tail_count = max_lines - head_count
    sampled = "\n".join(lines[:head_count] + ["... middle omitted for language detection ..."] + lines[-tail_count:])
    return sampled[:max_chars]


def _clone_response(response: ReviewResponse) -> ReviewResponse:
    return response.model_copy(deep=True)


def _language_metadata(selected_language: str | None, code: str | None) -> tuple[str | None, str | None, str | None]:
    if not selected_language:
        return None, None, None

    selected = selected_language.strip()
    selected_for_display = None if selected.lower() in {"auto", "auto-detect", "autodetect"} else selected
    detected = _detected_review_language(selected, code or "")
    return selected_for_display, detected, detected


def _canonical_language_name(value: str | None) -> str | None:
    if not value:
        return None

    normalized = re.sub(r"\s+", " ", value.strip().lower())
    normalized = normalized.replace("nodejs", "node.js")
    return _LANGUAGE_ALIASES.get(normalized)


def _language_from_ai_summary(summary: str) -> str | None:
    language_terms = sorted(
        set(_LANGUAGE_ALIASES.keys()) | {value.lower() for value in _LANGUAGE_ALIASES.values()},
        key=len,
        reverse=True,
    )
    language_pattern = "(" + "|".join(re.escape(term).replace(r"\ ", r"\s+") for term in language_terms) + ")"
    summary_lower = summary.lower()
    patterns = [
        rf"\b(?:the|this|provided|pasted)\s+{language_pattern}\s+(?:code|application|app|service|script|program)\b",
        rf"\bcode\s+(?:is|appears\s+to\s+be)\s+(?:written\s+in\s+)?{language_pattern}\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, summary_lower)
        if match:
            return _canonical_language_name(match.group(1))

    return None


def _ai_language_metadata(
    review: ReviewResponse,
    code: str | None = None,
    selected_language: str | None = None,
) -> tuple[str | None, str | None]:
    detected = _canonical_language_name(review.detected_language) or review.detected_language
    reviewed = _canonical_language_name(review.reviewed_language) or review.reviewed_language
    summary_language = _language_from_ai_summary(review.summary)
    syntax_language, syntax_confidence, _ = _syntax_language_detection(code or "") if code else (None, None, None)

    if summary_language and detected and _canonical_language_name(detected) != summary_language:
        detected = summary_language
        reviewed = summary_language
    elif summary_language and not detected:
        detected = summary_language
        reviewed = reviewed or summary_language
    elif (
        syntax_language
        and syntax_confidence
        and syntax_confidence >= 92
        and detected
        and _canonical_language_name(detected) != syntax_language
    ):
        detected = syntax_language
        reviewed = syntax_language
    elif detected == "SQL" and syntax_language and syntax_language not in {"SQL", "Auto", "Plain Text"}:
        detected = syntax_language
        reviewed = syntax_language

    return detected, reviewed or detected


def _cached_response(payload: ReviewRequest) -> ReviewResponse | None:
    entry = _REVIEW_CACHE.get(_cache_key(payload))
    if not entry:
        return None

    cached_review = _clone_response(entry["response"])
    cached_review.review_source = "cache"
    cached_review.cache_hit = True
    cached_review.similarity_used = 1.0
    return cached_review


def _find_similar_cached_review(payload: ReviewRequest) -> dict[str, Any] | None:
    current_code = _normalize_code_for_similarity(payload.code)
    if not current_code:
        return None

    best_entry: dict[str, Any] | None = None
    best_score = 0.0
    threshold = _similarity_threshold()
    language = payload.language.strip().lower()
    focus = payload.focus.strip().lower()

    for entry in _REVIEW_CACHE.values():
        if entry["language"] != language or entry["focus"] != focus:
            continue
        previous_code = _normalize_code_for_similarity(entry["code"])
        score = difflib.SequenceMatcher(None, previous_code, current_code).ratio()
        if score > best_score:
            best_score = score
            best_entry = entry

    if best_entry and best_score >= threshold:
        return {**best_entry, "similarity": round(best_score, 3)}

    return None


def _diff_summary(previous_code: str, current_code: str, max_lines: int = 24) -> str:
    diff_lines = list(
        difflib.unified_diff(
            previous_code.splitlines(),
            current_code.splitlines(),
            fromfile="previous",
            tofile="current",
            lineterm="",
            n=2,
        )
    )
    if not diff_lines:
        return "No text differences detected."

    selected_lines = diff_lines[:max_lines]
    if len(diff_lines) > max_lines:
        selected_lines.append("... diff truncated ...")
    return "\n".join(selected_lines)


def _similar_review_context(entry: dict[str, Any] | None, current_code: str) -> str:
    if not entry:
        return ""

    previous_review: ReviewResponse = entry["response"]
    bug_titles = [f"{bug.severity}: {bug.title}" for bug in previous_review.bugs[:6]]
    return f"""
Previous similar review context:
- Similarity: {entry["similarity"]:.0%}
- Previous summary: {previous_review.summary}
- Previous risk score: {previous_review.risk_score}/100
- Previous findings: {", ".join(bug_titles) if bug_titles else "No concrete findings"}

Changed code summary:
{_diff_summary(entry["code"], current_code)}

Use this previous review only as context. Review the full current code and return a fresh result for the current code.
"""


def _store_review(payload: ReviewRequest, response: ReviewResponse) -> None:
    key = _cache_key(payload)
    review_to_store = _clone_response(response)
    review_to_store.cache_hit = False
    _REVIEW_CACHE[key] = {
        "language": payload.language.strip().lower(),
        "focus": payload.focus.strip().lower(),
        "code": payload.code,
        "response": review_to_store,
        "created_at": time.time(),
    }

    while len(_REVIEW_CACHE) > _cache_limit():
        oldest_key = min(_REVIEW_CACHE, key=lambda cache_key: _REVIEW_CACHE[cache_key]["created_at"])
        _REVIEW_CACHE.pop(oldest_key, None)


def _clear_review_cache() -> None:
    _REVIEW_CACHE.clear()


def _apply_similar_cache_metadata(review: ReviewResponse, similar_cached_review: dict[str, Any] | None) -> ReviewResponse:
    if similar_cached_review:
        review.review_source = f"{review.review_source}_with_cache_context"
        review.similarity_used = similar_cached_review["similarity"]
    return review


def _fallback_after_ai_error(payload: ReviewRequest, similar_cached_review: dict[str, Any] | None = None) -> ReviewResponse:
    review = _fallback_review(payload)
    review.review_source = "fallback_after_ai_error"
    return _apply_similar_cache_metadata(review, similar_cached_review)


def _ai_user_prompt(payload: ReviewRequest, similar_cached_review: dict[str, Any] | None = None, retry_safe_mode: bool = False) -> str:
    local_detected_language = _detected_review_language(payload.language, payload.code)
    prompt_detected_language = "AI must detect this from the pasted code." if _is_auto_language(payload.language) else local_detected_language
    code_fence_language = "text" if _is_auto_language(payload.language) else local_detected_language
    return _ai_review_prompt(payload, prompt_detected_language, code_fence_language, similar_cached_review, retry_safe_mode)


def _ai_review_prompt(
    payload: ReviewRequest,
    detected_language: str,
    code_fence_language: str,
    similar_cached_review: dict[str, Any] | None = None,
    retry_safe_mode: bool = False,
) -> str:
    code_for_prompt = _redact_for_ai_retry(payload.code) if retry_safe_mode else payload.code
    language_selection_note = (
        f"Selected language from UI:\n{payload.language}"
        if not _is_auto_language(payload.language)
        else "Manual language selection:\nNot used. The previous AI step detected the pasted code language."
    )
    retry_instruction = ""
    if retry_safe_mode:
        retry_instruction = """
This is a defensive review retry. Some dangerous string literals were redacted only to keep the AI request safe.
Still review the code structure and identify likely security and reliability issues.
Do not provide exploit steps or executable attack commands.
"""

    return f"""
Review this code for a defensive software security/code quality audit.

{language_selection_note}

AI language detection step result:
{detected_language}

Review the code as this detected language before analyzing bugs.
If a manual selected language and detected language differ, review the actual pasted code using the detected language.
Mention the language mismatch only as a Low note if it matters.

Focus areas:
{payload.focus}

Code:
```{code_fence_language}
{code_for_prompt}
```

{retry_instruction}
{_similar_review_context(similar_cached_review, payload.code)}
"""


def _ai_language_detection_prompt(code: str, challenge_note: str | None = None) -> str:
    sample = _code_sample_for_language_detection(code)
    challenge_section = (
        f"""
Important correction challenge:
{challenge_note}
Re-read the code and return the strongest programming language candidate, not a data format or embedded string language.
"""
        if challenge_note
        else ""
    )
    return f"""
Detect the main programming language of this pasted code.
Return only JSON.
Do not review vulnerabilities in this step.
If application code contains SQL strings, return the application language, not SQL.
Return SQL only if the pasted code is primarily standalone SQL.
Return JSON only if the pasted text is primarily raw JSON data. If it has imports, functions, types, semicolons, method calls, or shell/database calls, it is executable code, not JSON.
Use this exact reasoning process before answering:
1. Read the pasted code.
2. Look for code-style fingerprints, such as @import("std") for Zig, Object subclass: for Smalltalk, import vibe.d for D, open Lwt for OCaml, and (defn ...) for Clojure.
3. Compare all syntax clues together instead of trusting one weak clue.
4. Pick the strongest language match and explain the strongest evidence briefly.
{challenge_section}
This is a short sample from the pasted code when the full input is large.

Code:
```text
{sample}
```
"""


def _language_detection_challenge_note(
    detected_language: str,
    code: str,
    syntax_language: str | None,
    syntax_confidence: int | None,
    syntax_evidence: str | None,
) -> str | None:
    canonical_detected = _canonical_language_name(detected_language) or detected_language
    code_lower = code.lower()
    executable = _code_looks_executable_not_data(code_lower)

    if canonical_detected in {"JSON", "SQL"} and executable:
        return (
            f"Your previous answer was {canonical_detected}, but this pasted text has executable code structure. "
            "JSON/SQL may appear inside strings or map literals, but the answer must be the host programming language."
        )

    if syntax_language and canonical_detected != syntax_language and (syntax_confidence or 0) >= 85:
        return (
            f"Your previous answer was {canonical_detected}. Independent syntax evidence suggests {syntax_language}: "
            f"{syntax_evidence or 'strong source-code fingerprints were present'}."
        )

    if (
        canonical_detected in {"JavaScript", "TypeScript", "C", "C++", "Plain Text", "Text", "Unknown"}
        and executable
        and canonical_detected != syntax_language
        and not _code_looks_like_node_js(code_lower)
    ):
        return (
            f"Your previous answer was {canonical_detected}. Re-check whether this is a superficially similar language, "
            "because braces, semicolons, imports, or arrow-style method calls alone are not enough."
        )

    return None


def _ai_local_findings_prompt(payload: ReviewRequest, safety_review: ReviewResponse) -> str:
    detected_language = _detected_review_language(payload.language, payload.code)
    language_selection_note = payload.language if not _is_auto_language(payload.language) else "Auto-detect"
    bug_lines = [
        f"- {bug.severity}: {bug.title}. {bug.explanation} Fix: {bug.suggested_fix}"
        for bug in safety_review.bugs
    ] or ["- No concrete local bug findings."]
    improvement_lines = [f"- {item}" for item in safety_review.improvements] or ["- No local improvement notes."]
    test_lines = [f"- {item}" for item in safety_review.test_cases] or ["- No local test ideas."]

    return f"""
The direct raw-code AI review failed, so complete a defensive AI review using this local static-analysis context.
Do not claim you executed the code.
Do not invent exploit steps.
Use the findings below as evidence and improve the wording, severity consistency, summary, and test ideas.
Always set fixed_code to null.
Return the same strict JSON structure.

Selected language from UI: {language_selection_note}
Detected code language: {detected_language}
Code size: {len(payload.code.splitlines())} lines
Focus areas: {payload.focus}

Local safety findings:
{chr(10).join(bug_lines)}

Local improvement notes:
{chr(10).join(improvement_lines)}

Local test ideas:
{chr(10).join(test_lines)}
"""


def _bug_findings(value: Any) -> list[BugFinding]:
    findings: list[BugFinding] = []
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            findings.append(
                BugFinding(
                    title=str(item.get("title") or "Potential issue").strip(),
                    severity=str(item.get("severity") or "Medium").strip(),
                    explanation=str(item.get("explanation") or "The AI review flagged this area for closer inspection.").strip(),
                    suggested_fix=str(item.get("suggested_fix") or "Review this code path and add a safer implementation.").strip(),
                )
            )

    return findings


def _is_placeholder_bug(bug: BugFinding) -> bool:
    placeholder_titles = {
        "no specific bug reported",
        "no critical issue detected by fallback engine",
    }
    return bug.title.strip().lower() in placeholder_titles


def _normalize_severity(severity: str) -> str:
    normalized = severity.strip().lower()
    if "critical" in normalized:
        return "Critical"
    if "high" in normalized:
        return "High"
    if "medium" in normalized or "moderate" in normalized:
        return "Medium"
    return "Low"


def _severity_floor(bugs: list[BugFinding]) -> int:
    severities = {_normalize_severity(bug.severity) for bug in bugs if not _is_placeholder_bug(bug)}
    if "Critical" in severities:
        return 80
    if "High" in severities:
        return 51
    if "Medium" in severities:
        return 30
    return 10


def _bug_text(bug: BugFinding) -> str:
    return " ".join([bug.title, bug.severity, bug.explanation, bug.suggested_fix]).lower()


def _bug_category(bug: BugFinding) -> str:
    text = _bug_text(bug)
    title = bug.title.lower()

    title_first_patterns = [
        ("language_mismatch", ["language mismatch", "language selection mismatch", "wrong language"]),
        ("path_traversal", ["path traversal", "directory traversal", "arbitrary file"]),
        ("event_loop_blocking", ["event loop", "synchronous file", "writefilesync"]),
        ("command_injection", ["command injection", "shell injection"]),
        ("sql_injection", ["sql injection"]),
        ("raw_card", ["raw card", "card number", "payment card", "credit card"]),
        ("ssrf", ["ssrf", "server-side request forgery"]),
        ("hardcoded_secret", ["hardcoded secret", "hardcoded api", "hardcoded key"]),
    ]

    for category, patterns in title_first_patterns:
        if any(pattern in title for pattern in patterns):
            return category

    category_patterns = [
        ("language_mismatch", ["language mismatch", "language selection mismatch", "wrong language", "not python", "not javascript", "selected language", "code is node.js", "code is javascript"]),
        ("event_loop_blocking", ["event loop blocking", "writefilesync", "synchronous file"]),
        ("plaintext_password", ["plain-text password", "plaintext password", "password comparison"]),
        ("weak_admin", ["admin key", "adminkey", "weak admin", "admin authorization"]),
        ("sql_injection", ["sql injection", "parameterized quer", "prepared statement", "unsafe query"]),
        ("raw_card", ["raw card", "card number", "card_number", "cardnumber", "credit card", "payment card", "cvv", "pci"]),
        ("path_traversal", ["path traversal", "arbitrary file", "unsafe filename", "file write", "directory traversal"]),
        ("command_injection", ["command injection", "child_process", "shell command", "shell execution", "exec(", "execsync", "system command"]),
        ("dynamic_execution", ["eval", "dynamic execution", "arbitrary code"]),
        ("hardcoded_secret", ["hardcoded", "api key", "secret", "token", "password", "admin_token", "payment_token", "jwt_secret", "admin_key"]),
        ("ssrf", ["ssrf", "server-side request forgery", "webhook url", "webhookurl", "user-controlled url", "callback url"]),
        ("type_mismatch", ["typeerror", "type error", "type mismatch", "non-numeric", "invalid data type"]),
        ("negative_payment", ["negative refund", "negative payment", "refund amount", "payment amount", "discount range"]),
        ("collection_none", ["fetchone", "none check", "empty collection", "index access", "list index", "array access", "rows[0]", "results[0]"]),
        ("request_exception", ["request exception", "network exception", "without exception handling", "raise_for_status", "axios.post call missing error"]),
        ("request_timeout", ["missing timeout", "without a timeout"]),
        ("db_connection", ["connection not closed", "db connection", "database connection", "delete_user"]),
        ("db_query_error", ["db.query", "database error", "query error", "ignored error"]),
        ("transaction_rollback", ["rollback", "transaction", "multi-step database"]),
        ("datetime_json", ["datetime", "json serial", "json.dumps", "created_at"]),
        ("missing_auth", ["missing authentication", "unauthenticated", "without authentication", "auth middleware"]),
        ("off_by_one", ["off-by-one", "<= items.length", "<= array.length", "out of bounds"]),
        ("info_leakage", ["err.message", "raw error", "error message returned", "information leakage"]),
        ("weak_error_handling", ["weak error handling", "bare except", "broad exception"]),
        ("null_pointer", ["null pointer", "nullptr", "null reference"]),
        ("html_injection", ["html injection", "xss", "dangerouslysetinnerhtml", "innerhtml"]),
        ("division_by_zero", ["division by zero", "denominator", "divide by zero"]),
    ]

    for category, patterns in category_patterns:
        if any(pattern in text for pattern in patterns):
            return category

    return _bug_title_key(bug.title)


def _dedupe_bug_findings(bugs: list[BugFinding]) -> list[BugFinding]:
    deduped: list[BugFinding] = []
    seen_categories: set[str] = set()

    for bug in bugs:
        if _is_placeholder_bug(bug):
            continue
        category = _bug_category(bug)
        if category in seen_categories:
            continue
        seen_categories.add(category)
        deduped.append(bug)

    return deduped


def _clean_test_cases(test_cases: list[str]) -> list[str]:
    deduped = _dedupe_strings(test_cases)
    specific_tests = [item for item in deduped if item.strip().lower() not in _GENERIC_TEST_CASES]
    return specific_tests or deduped


def _score_security_payment_context(bugs: list[BugFinding]) -> int | None:
    categories = {_bug_category(bug) for bug in bugs}
    critical_count = sum(1 for bug in bugs if bug.severity == "Critical")

    if {"sql_injection", "command_injection", "raw_card"}.issubset(categories):
        return 100
    if "sql_injection" in categories and ("raw_card" in categories or "hardcoded_secret" in categories):
        return 100
    if critical_count >= 2:
        return 95
    return None


def _normalize_review_response(
    review: ReviewResponse,
    selected_language: str | None = None,
    code: str | None = None,
) -> ReviewResponse:
    code_lower = (code or "").lower()
    selected_language_value, detected_language_value, reviewed_language_value = _language_metadata(selected_language, code)
    syntax_language, syntax_confidence, syntax_evidence = _syntax_language_detection(code or "")
    if review.used_ai:
        ai_detected_language, ai_reviewed_language = _ai_language_metadata(review, code, selected_language)
        detected_language_value = ai_detected_language or detected_language_value
        reviewed_language_value = ai_reviewed_language or reviewed_language_value
    if syntax_language and detected_language_value == syntax_language:
        language_confidence = review.language_detection_confidence or syntax_confidence
        language_evidence = review.language_detection_evidence or syntax_evidence
    else:
        language_confidence = review.language_detection_confidence or (70 if review.used_ai and detected_language_value else None)
        language_evidence = review.language_detection_evidence or (
            "AI language detection based on the pasted code." if review.used_ai and detected_language_value else None
        )
    language_source = review.language_detection_source or ("ai" if review.used_ai else "fallback")
    if review.used_ai and syntax_language and detected_language_value == syntax_language:
        language_source = "ai+syntax"
    normalized_bugs: list[BugFinding] = []

    for bug in review.bugs:
        if _is_placeholder_bug(bug):
            continue

        normalized_bug = BugFinding(
            title=bug.title,
            severity=_normalize_severity(bug.severity),
            explanation=bug.explanation,
            suggested_fix=bug.suggested_fix,
        )

        if _bug_category(normalized_bug) == "language_mismatch":
            if _is_auto_language(selected_language) or _selected_language_matches_code(selected_language, code_lower):
                continue
            normalized_bug = BugFinding(
                title="Language selection mismatch note",
                severity="Low",
                explanation=normalized_bug.explanation,
                suggested_fix="Choose the closest language before reviewing so syntax-specific checks are more accurate.",
            )

        normalized_bugs.append(normalized_bug)

    real_bugs = _dedupe_bug_findings(normalized_bugs)

    risk_score = _coerce_risk_score(review.risk_score)
    if real_bugs:
        risk_score = max(risk_score, _severity_floor(real_bugs))
        security_payment_floor = _score_security_payment_context(real_bugs)
        if security_payment_floor is not None:
            risk_score = max(risk_score, security_payment_floor)
        if all(bug.severity == "Low" for bug in real_bugs):
            risk_score = min(max(risk_score, 10), 25)
    else:
        risk_score = min(max(risk_score, 10), 25)

    return ReviewResponse(
        summary=review.summary,
        risk_score=risk_score,
        bugs=real_bugs,
        improvements=_dedupe_strings(review.improvements),
        test_cases=_clean_test_cases(review.test_cases),
        fixed_code=review.fixed_code,
        used_ai=review.used_ai,
        review_source=review.review_source,
        selected_language=selected_language_value or review.selected_language,
        detected_language=detected_language_value or review.detected_language,
        reviewed_language=reviewed_language_value or review.reviewed_language,
        language_detection_source=language_source,
        language_detection_confidence=language_confidence,
        language_detection_evidence=language_evidence,
        cache_hit=review.cache_hit,
        similarity_used=review.similarity_used,
    )


def _bug_title_key(title: str) -> str:
    normalized = title.lower()
    normalized = re.sub(r"\b(possible|potential)\b", "", normalized)
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _user_input_variables(code_lower: str) -> set[str]:
    variables: set[str] = set()

    for destructured in re.findall(r"(?:const|let|var)?\s*\{([^}]+)\}\s*=\s*req\.(?:body|query|params)", code_lower):
        for name in destructured.split(","):
            variable = name.split(":")[0].strip().strip("{}[]")
            if variable:
                variables.add(variable)

    for match in re.finditer(
        r"(?:const|let|var)\s+(\w+)\s*=\s*req\.(?:body|query|params)(?:\.(\w+)|\[['\"](\w+)['\"]\])?",
        code_lower,
    ):
        variables.add(match.group(1))
        for group in match.groups()[1:]:
            if group:
                variables.add(group)

    variables.update(re.findall(r"req\.(?:body|query|params)\.(\w+)", code_lower))
    return variables


def _contains_user_input(text: str, user_vars: set[str]) -> bool:
    return "req.body" in text or "req.query" in text or "req.params" in text or any(re.search(rf"\b{re.escape(var)}\b", text) for var in user_vars)


def _has_hardcoded_secret(code_lower: str) -> bool:
    secret_name = r"(password|api[_-]?key|secret|token|jwt_secret|admin_key|adminkey|db_password|database_password|payment_secret|payment_token)"
    return bool(
        re.search(rf"\b[\w]*{secret_name}[\w]*\s*=", code_lower)
        or re.search(rf"\b[\w]*{secret_name}[\w]*\s*\n\s*\^\s*['\"]", code_lower)
    )


def _has_unsafe_sql_construction(code_lower: str) -> bool:
    has_sql = re.search(r"\b(select|insert|update|delete)\b", code_lower)
    has_concat_or_format = re.search(
        r"(\+\s*\w+|~\s*\w+|\.\.\s*[\w.]+|\^\s*[\w(]|f[\"']|`[^`]*\$\{|\$\{|#\{|\.format\s*\(|std\.fmt\.allocprint\s*\(|%\s*\(|req\.(?:body|query|params)|params\s*\[)",
        code_lower,
        re.DOTALL,
    )
    smalltalk_sql_concat = re.search(
        r"(?:\w*sql\s*:=|execute:)\s*'[\s\S]{0,600}\b(select|insert|update|delete)\b[\s\S]{0,600},\s*\w+",
        code_lower,
    )
    return bool(has_sql and has_concat_or_format or smalltalk_sql_concat)


def _has_raw_card_handling(code_lower: str) -> bool:
    return bool(re.search(r"\b(card_number|cardnumber|credit_card|creditcard|pan|cvv)\b", code_lower))


def _has_unsafe_path_construction(code_lower: str) -> bool:
    user_vars = _user_input_variables(code_lower)
    has_file_write = re.search(r"\b(open|open_out|open_in|write_text|write_bytes|io\.open)\s*\(", code_lower) or ".save(" in code_lower
    has_file_write = has_file_write or re.search(r"\b(open_out|open_in)\s+\w+", code_lower)
    has_file_write = has_file_write or "fs.writefile" in code_lower or "fs.promises.writefile" in code_lower
    has_file_write = has_file_write or re.search(r"\bnew\s+file\s*\(|\.text\s*=", code_lower)
    has_file_write = has_file_write or re.search(r"\b(write|writefile|readtext|readfilealloc)\s*\(", code_lower)
    has_file_write = has_file_write or "filestream" in code_lower
    has_user_filename = re.search(r"\b(filename|file_name|path|upload|user_input|email)\b", code_lower) or bool({"filename", "file_name", "path", "email", "upload"} & user_vars)
    has_path_join = "os.path.join" in code_lower or "pathlib.path" in code_lower or "path.join" in code_lower or "/" in code_lower
    return bool(has_file_write and has_user_filename and has_path_join)


def _has_js_command_injection(code_lower: str) -> bool:
    user_vars = _user_input_variables(code_lower)
    has_shell_exec = bool(re.search(r"(child_process\.)?(exec|execsync)\s*\(|\bexec\s*\(|\bsystem\s*\(", code_lower))
    if not has_shell_exec and "require('child_process')" not in code_lower and 'require("child_process")' not in code_lower:
        return False

    command_assignments = re.findall(r"(?:const|let|var)\s+\w*command\w*\s*=\s*([^;\n]+)", code_lower)
    exec_calls = re.findall(r"(?:exec|execsync|system)\s*\(([^)\n]+)", code_lower)
    suspicious_parts = command_assignments + exec_calls
    return any("${" in part or "+" in part or _contains_user_input(part, user_vars) for part in suspicious_parts) or _contains_user_input(code_lower, user_vars)


def _has_shell_command_injection(code_lower: str) -> bool:
    user_vars = _user_input_variables(code_lower) | {
        "backup_name",
        "backupname",
        "filename",
        "file_name",
        "path",
        "config_path",
        "command",
    }
    has_shell_exec = bool(
        re.search(
            r"(os\.execute|sys\.command|system\s*\(|execmd\s*\(|executeshell\s*\(|system\.cmd\s*\(|system\.cmd|system\.cmd\(|exec_cmd\s*\(|std\.process\.child\.run|process\.create_process|subprocess\.|:\s*os\.cmd|\.execute\s*\(\s*\)|\bsh\s+\"sh\"\s+\"-c\")",
            code_lower,
        )
        or "osprocess command:" in code_lower
    )
    if not has_shell_exec:
        return False

    command_assignments = re.findall(
        r"(?:local\s+|let\s+|my\s+|const\s+|var\s+|final\s+)?\w*command\w*\s*:?\s*=\s*([^;\n]+)",
        code_lower,
    )
    shell_calls = re.findall(r"(?:os\.execute|system|execmd|executeshell|exec_cmd|system\.cmd|\.execute)\s*\(([^)\n]+)", code_lower)
    shell_calls.extend(re.findall(r"osprocess\s+command:\s*([^\n.]+)", code_lower))
    suspicious_parts = command_assignments + shell_calls
    if "std.process.child.run" in code_lower and re.search(r"[\"']sh[\"']\s*,\s*[\"']-c[\"']", code_lower):
        return True
    return any(
        any(marker in part for marker in ["..", "+", "~", ",", "#{", "${", "sh -c"])
        or _contains_user_input(part, user_vars)
        for part in suspicious_parts
    )


def _has_axios_post(code_lower: str) -> bool:
    return "axios.post" in code_lower


def _has_js_ssrf(code_lower: str) -> bool:
    if not _has_axios_post(code_lower):
        return False

    user_vars = _user_input_variables(code_lower)
    for first_arg in re.findall(r"axios\.post\s*\(\s*([^,\n)]+)", code_lower):
        if _contains_user_input(first_arg, user_vars):
            return True
        if re.search(r"\b(webhookurl|callbackurl|targeturl|url)\b", first_arg) and _contains_user_input(code_lower, user_vars):
            return True

    return False


def _has_axios_missing_error_handling(code_lower: str) -> bool:
    return _has_axios_post(code_lower) and "try" not in code_lower and ".catch(" not in code_lower


def _has_js_plaintext_password_check(code_lower: str) -> bool:
    has_login = "login" in code_lower or "password" in code_lower
    has_sql_password = bool(re.search(r"where[^;\n]*(password|pwd)[^;\n]*(\+|\$\{|req\.)", code_lower))
    has_direct_compare = bool(re.search(r"(user|row|rows\[0\]|result)\.password\s*(===|==)\s*password", code_lower))
    return has_login and (has_sql_password or has_direct_compare)


def _has_missing_express_auth(code_lower: str) -> bool:
    route_pattern = r"(app|router)\.(post|put|patch|delete)\s*\(\s*['\"][^'\"]*(admin|refund|payment|order|delete|user)"
    if not re.search(route_pattern, code_lower):
        return False
    return not re.search(r"(auth|authenticate|authorize|requireauth|verifytoken|jwt\.verify)", code_lower)


def _has_weak_admin_key_check(code_lower: str) -> bool:
    return bool(re.search(r"\b(admin_key|adminkey|admin-key|x-admin-key)\b", code_lower))


def _has_js_off_by_one_loop(code_lower: str) -> bool:
    return bool(re.search(r"for\s*\([^;]+;\s*\w+\s*<=\s*[\w.]+\.length\s*;", code_lower))


def _has_unchecked_js_first_row(code_lower: str) -> bool:
    if not re.search(r"\b(rows|results|result)\s*\[\s*0\s*\]", code_lower):
        return False
    has_guard = re.search(r"if\s*\(\s*(!\s*(rows|results|result)|(rows|results|result)\.length\s*(===|==|>|>=)\s*0)", code_lower)
    return not bool(has_guard)


def _has_db_query_without_error_handling(code_lower: str) -> bool:
    if "db.query" not in code_lower:
        return False
    return "err" not in code_lower and "error" not in code_lower or not re.search(r"if\s*\(\s*(err|error)|catch\s*\(", code_lower)


def _has_raw_error_message_return(code_lower: str) -> bool:
    return bool(re.search(r"res\.(status\([^)]*\)\.)?(json|send)\s*\([^;\n]*(err|error)\.message", code_lower))


def _has_sync_file_write_in_express_route(code_lower: str) -> bool:
    has_route = bool(re.search(r"(app|router)\.(get|post|put|patch|delete)\s*\(", code_lower))
    return has_route and "fs.writefilesync" in code_lower


def _has_requests_post(code_lower: str) -> bool:
    return "requests.post" in code_lower


def _has_db_connection_without_close(code_lower: str) -> bool:
    has_connection = "sqlite3.connect" in code_lower or "get_db_connection" in code_lower or "db.connect" in code_lower
    if not has_connection:
        return False
    if "with sqlite3.connect" in code_lower or "with get_db_connection" in code_lower:
        return False
    if ".close(" not in code_lower:
        return True
    return "def delete_user" in code_lower and "return" in code_lower and "finally" not in code_lower


def _has_missing_transaction_rollback(code_lower: str) -> bool:
    python_write_count = len(re.findall(r"\.execute\s*\(\s*[f]?[\"']\s*(insert|update|delete)\b", code_lower))
    js_write_count = len(re.findall(r"\b(insert|update|delete)\b", code_lower)) if "db.query" in code_lower else 0
    has_commit = ".commit(" in code_lower or "commit(" in code_lower
    return (python_write_count >= 2 and has_commit or js_write_count >= 2) and "rollback" not in code_lower


def _has_datetime_json_risk(code_lower: str) -> bool:
    return "json.dumps" in code_lower and ("datetime" in code_lower or "created_at" in code_lower)


def _has_unchecked_fetchone(code_lower: str) -> bool:
    if ".fetchone(" not in code_lower:
        return False

    assigned_results = re.findall(r"(\w+)\s*=\s*[^\n]*\.fetchone\s*\(", code_lower)
    if not assigned_results:
        return True

    for variable in assigned_results:
        guard_pattern = rf"if\s+(not\s+{re.escape(variable)}|{re.escape(variable)}\s+is\s+none|{re.escape(variable)}\s*==\s*none)"
        if not re.search(guard_pattern, code_lower):
            return True

    return False


def _has_negative_payment_amount(code_lower: str) -> bool:
    has_payment_context = re.search(r"\b(refund|payment|charge|amount|total)\b", code_lower)
    has_negative_path = re.search(
        r"(<\s*0|-\s*amount|amount\s*=\s*-|refund_amount\s*=\s*-|refundamount\s*=\s*-|payment_amount\s*=\s*-|paymentamount\s*=\s*-)",
        code_lower,
    )
    return bool(has_payment_context and has_negative_path)


def _merge_safety_checks(
    ai_review: ReviewResponse,
    safety_review: ReviewResponse,
    selected_language: str | None = None,
    code: str | None = None,
) -> ReviewResponse:
    ai_review = _normalize_review_response(ai_review, selected_language, code)
    safety_review = _normalize_review_response(safety_review, selected_language, code)
    safety_bugs = [bug for bug in safety_review.bugs if not _is_placeholder_bug(bug)]
    ai_bugs = [bug for bug in ai_review.bugs if not _is_placeholder_bug(bug)]

    seen_bug_categories = {_bug_category(bug) for bug in ai_bugs}
    merged_bugs = list(ai_bugs)
    added_safety_count = 0
    for bug in safety_bugs:
        bug_category = _bug_category(bug)
        if bug_category not in seen_bug_categories:
            merged_bugs.append(bug)
            seen_bug_categories.add(bug_category)
            added_safety_count += 1

    improvements = _dedupe_strings(ai_review.improvements + safety_review.improvements)
    test_cases = _dedupe_strings(ai_review.test_cases + safety_review.test_cases)
    summary = ai_review.summary
    if added_safety_count:
        summary = f"AI review completed. Local safety checks also flagged {added_safety_count} additional issue(s)."

    return _normalize_review_response(ReviewResponse(
        summary=summary,
        risk_score=max(ai_review.risk_score, safety_review.risk_score),
        bugs=merged_bugs,
        improvements=improvements,
        test_cases=test_cases,
        fixed_code=ai_review.fixed_code,
        used_ai=True,
        review_source="ai",
        selected_language=ai_review.selected_language,
        detected_language=ai_review.detected_language,
        reviewed_language=ai_review.reviewed_language,
        language_detection_source=ai_review.language_detection_source,
    ), selected_language, code)


def _fallback_review(payload: ReviewRequest, reason: str | None = None) -> ReviewResponse:
    """Local rules keep the demo usable when no AI API key is configured."""
    code = payload.code
    language = payload.language.lower()
    code_lower = code.lower()
    is_javascript_review = _is_javascript_language(language) or _code_looks_like_node_js(code_lower)
    bugs: list[BugFinding] = []
    improvements: list[str] = []
    test_cases: list[str] = []
    risk_score = 15

    if _has_hardcoded_secret(code_lower):
        is_payment_or_admin_secret = bool(re.search(r"(admin|payment|stripe|paypal|jwt|database|db|secret|token)", code_lower))
        bugs.append(
            BugFinding(
                title="Hardcoded payment/admin secret in source code" if is_payment_or_admin_secret else "Hardcoded secret in source code",
                severity="High",
                explanation="The code appears to assign a password, API key, token, or secret directly in source code.",
                suggested_fix="Move secrets to environment variables and never commit them to GitHub.",
            )
        )
        risk_score += 25

    if "except:" in code or ("catch (" in code and "console.log" not in code and "throw" not in code):
        bugs.append(
            BugFinding(
                title="Weak error handling",
                severity="Medium",
                explanation="The code may catch errors too broadly or without clear handling.",
                suggested_fix="Catch specific exceptions and log enough context to debug safely.",
            )
        )
        risk_score += 15

    if _has_unsafe_sql_construction(code_lower):
        bugs.append(
            BugFinding(
                title="SQL injection from unsafe query construction",
                severity="Critical",
                explanation="The code appears to build SQL using string concatenation, formatting, or an f-string with external values.",
                suggested_fix="Use parameterized queries and never insert user-controlled values directly into SQL strings.",
            )
        )
        risk_score += 40

    has_command_injection = (
        _has_js_command_injection(code_lower)
        if is_javascript_review
        else _has_shell_command_injection(code_lower)
    )
    if has_command_injection:
        command_title = (
            "Command injection from child_process.exec"
            if is_javascript_review
            else "Command injection from shell command construction"
        )
        bugs.append(
            BugFinding(
                title=command_title,
                severity="Critical",
                explanation="The code builds a shell command from user-controlled data before passing it to a shell execution API.",
                suggested_fix="Avoid shell execution for user input. Use safe library calls or spawn/execFile with a fixed command and validated arguments.",
            )
        )
        risk_score += 40

    if _has_raw_card_handling(code_lower):
        bugs.append(
            BugFinding(
                title="Raw card number handling without tokenization",
                severity="Critical",
                explanation="The code appears to handle raw card numbers or CVV data directly, which creates serious payment security and PCI compliance risk.",
                suggested_fix="Use a payment provider tokenization flow and avoid storing, logging, or transmitting raw card data in application code.",
            )
        )
        risk_score += 40

    if is_javascript_review and _has_js_ssrf(code_lower):
        bugs.append(
            BugFinding(
                title="SSRF risk from user-controlled webhook URL",
                severity="High",
                explanation="The code sends axios.post to a URL that appears to come from request body, query, or params, allowing attackers to make the server call internal or unexpected URLs.",
                suggested_fix="Allow-list trusted webhook hosts, reject private/internal IP ranges, and avoid sending server-side requests to arbitrary user-provided URLs.",
            )
        )
        risk_score += 30

    if _has_unsafe_path_construction(code_lower):
        bugs.append(
            BugFinding(
                title="Path traversal risk in file path construction",
                severity="Critical" if re.search(r"\b(open|write_text|write_bytes)\s*\(", code_lower) else "High",
                explanation="The code appears to construct a file path from user-controlled filename or path data before writing or saving a file.",
                suggested_fix="Normalize and validate filenames, reject path separators, and write only inside an allowed directory.",
            )
        )
        risk_score += 35

    if is_javascript_review and _has_axios_post(code_lower) and "timeout" not in code_lower:
        bugs.append(
            BugFinding(
                title="axios.post call missing timeout",
                severity="Medium",
                explanation="The code sends an HTTP request without a timeout, so a slow external service can hang the route or background task.",
                suggested_fix="Pass a timeout option to axios.post and handle timeout failures explicitly.",
            )
        )
        risk_score += 15

    if is_javascript_review and _has_axios_missing_error_handling(code_lower):
        bugs.append(
            BugFinding(
                title="axios.post call missing error handling",
                severity="Medium",
                explanation="The code sends an HTTP request without try/catch or a .catch handler for network errors and non-success responses.",
                suggested_fix="Wrap external requests in try/catch, handle AxiosError cases, and return a controlled response.",
            )
        )
        risk_score += 15

    if _has_requests_post(code_lower) and "timeout=" not in code_lower:
        bugs.append(
            BugFinding(
                title="requests.post call missing timeout",
                severity="Medium",
                explanation="The code sends an HTTP request without a timeout, so a slow external service can hang the request indefinitely.",
                suggested_fix="Pass a reasonable timeout to requests.post, such as timeout=10, and tune it for the external service.",
            )
        )
        risk_score += 15

    if _has_requests_post(code_lower) and "except" not in code_lower:
        bugs.append(
            BugFinding(
                title="requests.post call missing exception handling",
                severity="Medium",
                explanation="The code sends an HTTP request but does not appear to handle network errors, timeouts, or non-success responses.",
                suggested_fix="Wrap the call in targeted exception handling and call raise_for_status or handle unsuccessful status codes explicitly.",
            )
        )
        risk_score += 15

    if is_javascript_review and _has_js_plaintext_password_check(code_lower):
        bugs.append(
            BugFinding(
                title="Plain-text password comparison in login flow",
                severity="High",
                explanation="The code appears to compare passwords directly or include a password in SQL login logic instead of using password hashing.",
                suggested_fix="Store salted password hashes and verify with bcrypt, argon2, or another password hashing library.",
            )
        )
        risk_score += 25

    if is_javascript_review and _has_missing_express_auth(code_lower):
        bugs.append(
            BugFinding(
                title="Missing authentication on sensitive Express route",
                severity="High",
                explanation="A sensitive route such as payment, refund, admin, order, or delete appears to run without authentication middleware.",
                suggested_fix="Require authentication middleware and server-side authorization checks before executing sensitive actions.",
            )
        )
        risk_score += 25

    if is_javascript_review and _has_weak_admin_key_check(code_lower):
        bugs.append(
            BugFinding(
                title="Weak adminKey-only authorization",
                severity="High",
                explanation="The code appears to authorize admin actions using only a shared admin key, which is easy to leak and hard to audit.",
                suggested_fix="Use authenticated users, roles/permissions, key rotation, and audit logging instead of a single shared admin key.",
            )
        )
        risk_score += 25

    if _has_db_connection_without_close(code_lower):
        bugs.append(
            BugFinding(
                title="Database connection may not close on early return",
                severity="High" if "def delete_user" in code_lower else "Medium",
                explanation="The code opens a database connection without a clear finally block, context manager, or guaranteed close path.",
                suggested_fix="Use a context manager or close the connection in a finally block so early returns and exceptions do not leak connections.",
            )
        )
        risk_score += 25

    if _has_missing_transaction_rollback(code_lower):
        bugs.append(
            BugFinding(
                title="Missing transaction rollback for multi-step database write",
                severity="High",
                explanation="The code performs multiple database write operations and commits, but does not appear to roll back if one step fails.",
                suggested_fix="Wrap related writes in one transaction and call rollback in the exception path before re-raising or returning an error.",
            )
        )
        risk_score += 25

    if is_javascript_review and _has_db_query_without_error_handling(code_lower):
        bugs.append(
            BugFinding(
                title="db.query call missing error handling",
                severity="Medium",
                explanation="The code uses db.query without checking callback errors or handling query failures.",
                suggested_fix="Check the error argument in every callback or use async database calls with try/catch.",
            )
        )
        risk_score += 15

    if _has_datetime_json_risk(code_lower):
        bugs.append(
            BugFinding(
                title="Datetime JSON serialization risk",
                severity="Medium",
                explanation="json.dumps does not serialize datetime objects by default, so exporting values such as created_at can crash or produce invalid output.",
                suggested_fix="Convert datetime values to ISO strings before json.dumps or provide a safe serializer.",
            )
        )
        risk_score += 15

    if _has_unchecked_fetchone(code_lower):
        bugs.append(
            BugFinding(
                title="fetchone result used without None check",
                severity="High" if "delete_user" in code_lower or "payment" in code_lower else "Medium",
                explanation="The code calls fetchone but does not appear to check whether the query returned a row before using the result.",
                suggested_fix="Check for None immediately after fetchone and return a clear not-found response before indexing or dereferencing the row.",
            )
        )
        risk_score += 20

    if is_javascript_review and _has_unchecked_js_first_row(code_lower):
        bugs.append(
            BugFinding(
                title="rows[0] used without checking query results",
                severity="High",
                explanation="The code reads rows[0] or results[0] without checking whether the query returned any rows.",
                suggested_fix="Check that the result array exists and has at least one item before reading index 0.",
            )
        )
        risk_score += 20

    if _has_negative_payment_amount(code_lower):
        bugs.append(
            BugFinding(
                title="Negative refund/payment amount business logic risk",
                severity="High",
                explanation="The code appears to allow or create a negative refund, payment, charge, or amount value, which can break payment and accounting logic.",
                suggested_fix="Validate payment and refund amounts before processing, reject negative values, and define explicit business rules for credits.",
            )
        )
        risk_score += 30

    if is_javascript_review and _has_js_off_by_one_loop(code_lower):
        bugs.append(
            BugFinding(
                title="Off-by-one loop can read past array length",
                severity="High",
                explanation="A for loop uses <= items.length, which will access one index past the end of the array.",
                suggested_fix="Use i < items.length and add tests for empty and single-item arrays.",
            )
        )
        risk_score += 20

    if is_javascript_review and _has_raw_error_message_return(code_lower):
        bugs.append(
            BugFinding(
                title="Raw error message returned to client",
                severity="Medium",
                explanation="The route appears to return err.message or error.message directly, which can leak implementation details to users.",
                suggested_fix="Log detailed errors server-side and return a generic client-safe error message.",
            )
        )
        risk_score += 15

    if is_javascript_review and _has_sync_file_write_in_express_route(code_lower):
        bugs.append(
            BugFinding(
                title="Synchronous file write inside Express route",
                severity="Medium",
                explanation="fs.writeFileSync blocks the Node.js event loop when used inside a request handler.",
                suggested_fix="Use async fs.promises.writeFile or move blocking work to a background job.",
            )
        )
        risk_score += 15

    has_division = _has_probable_division(code)
    code_without_strings = re.sub(r"(['\"])(?:\\.|(?!\1).)*\1", "\"\"", code)
    has_arithmetic = bool(re.search(r"[\w)\]]+\s*[-*/]\s*[\w(\[]+", code_without_strings))
    calls_function_with_string = bool(re.search(r"\w+\s*\([^)]*['\"][^'\"]+['\"][^)]*\)", code))

    if re.search(r"/\s*0\b", code) or (has_division and re.search(r"\([^)]*,\s*0\s*\)", code)):
        bugs.append(
            BugFinding(
                title="Potential division by zero",
                severity="High",
                explanation="The code divides values and appears to call the function with zero or divide by zero directly.",
                suggested_fix="Validate the denominator before division and return a clear error for zero values.",
            )
        )
        risk_score += 25

    if language in ["python", "py"] and has_arithmetic and calls_function_with_string:
        bugs.append(
            BugFinding(
                title="Type mismatch risk in arithmetic calculation",
                severity="High",
                explanation="The code performs arithmetic and the sample call passes a string value, which can cause a runtime type error or invalid calculation.",
                suggested_fix="Validate numeric inputs before arithmetic and return a clear error for non-numeric values.",
            )
        )
        risk_score += 25

    if "discount" in code_lower and re.search(r"final_price\s*<\s*0|return\s+0", code_lower):
        bugs.append(
            BugFinding(
                title="Discount range business rule issue",
                severity="Medium",
                explanation="The code clamps negative prices to zero, but it does not validate whether discount values outside the expected range are allowed.",
                suggested_fix="Validate the discount range, for example 0 <= discount <= 1, or document the intended business rule.",
            )
        )
        risk_score += 15

    if re.search(r"\[[0-9]+\]", code) and re.search(r"\(\s*\[\s*\]\s*\)|=\s*\[\s*\]", code):
        bugs.append(
            BugFinding(
                title="Unchecked collection/index access",
                severity="High",
                explanation="The code indexes into a list or array while also showing an empty collection case.",
                suggested_fix="Check that the collection has enough items before reading by index.",
            )
        )
        risk_score += 20

    if language in ["python", "py"] and re.search(r"\b(eval|exec)\s*\(", code):
        bugs.append(
            BugFinding(
                title="Unsafe dynamic execution",
                severity="Critical",
                explanation="eval or exec can execute arbitrary input and create serious security risk.",
                suggested_fix="Replace dynamic execution with explicit parsing or a safe allow-list of operations.",
            )
        )
        risk_score += 35

    if re.search(r"\bgroovyshell\s*\(|\.evaluate\s*\(|\bcompiler\s+evaluate:", code_lower):
        bugs.append(
            BugFinding(
                title="Unsafe dynamic code execution",
                severity="Critical",
                explanation="The code evaluates file or request-controlled content dynamically, which can execute arbitrary code.",
                suggested_fix="Do not evaluate untrusted scripts or configuration files. Use a strict parser, schema validation, or a signed allow-list of configuration files.",
            )
        )
        risk_score += 40

    if is_javascript_review and ("innerHTML" in code or "dangerouslySetInnerHTML" in code):
        bugs.append(
            BugFinding(
                title="Possible unsafe HTML injection",
                severity="High",
                explanation="Writing raw HTML into the DOM can allow cross-site scripting when data is not trusted.",
                suggested_fix="Render text safely or sanitize trusted HTML with a vetted sanitizer.",
            )
        )
        risk_score += 25

    if language in ["typescript", "react"] and re.search(r"\w+\??\s*:\s*[^)\n]+", code) and re.search(r"\w+\.\w+", code):
        bugs.append(
            BugFinding(
                title="Possible missing null or undefined check",
                severity="Medium",
                explanation="The code accesses a property on a value that appears optional or nullable.",
                suggested_fix="Add a guard clause, optional chaining, or make the parameter required.",
            )
        )
        risk_score += 15

    if language in ["java", "c++", "cpp"] and re.search(r"=\s*null\b|=\s*nullptr\b", code) and re.search(r"\.\w+\s*\(|->\w+\s*\(", code):
        bugs.append(
            BugFinding(
                title="Possible null pointer risk",
                severity="High",
                explanation="The code assigns a null value and later calls a method or member through an object reference.",
                suggested_fix="Validate the object before dereferencing it or avoid passing null into the function.",
            )
        )
        risk_score += 25

    if language == "sql" and _has_unsafe_sql_construction(code_lower):
        bugs.append(
            BugFinding(
                title="SQL injection from unsafe query construction",
                severity="Critical",
                explanation="The query appears to build SQL with string interpolation or concatenation.",
                suggested_fix="Use parameterized queries instead of building SQL strings from user input.",
            )
        )
        risk_score += 35

    if language in ["python", "py"] and "print(" in code:
        improvements.append("Replace print statements with structured logging for production code.")

    if is_javascript_review and "var " in code:
        improvements.append("Use let or const instead of var to avoid function-scope issues.")

    if len(code.splitlines()) > 80:
        improvements.append("Consider splitting this code into smaller functions or modules.")

    if has_division:
        improvements.append("Add explicit validation around arithmetic edge cases such as zero, null, or missing values.")

    if "discount" in code_lower:
        improvements.append("Document and validate the expected discount range so business rules are explicit.")

    if not improvements:
        improvements.append("Add comments for complex logic and keep function names clear.")

    test_cases.extend(
        [
            "Test normal valid input.",
            "Test empty or missing input.",
            "Test invalid data type input.",
            "Test boundary values and large input.",
            "Test error-handling behavior.",
        ]
    )

    if has_division:
        test_cases.append("Test division or arithmetic behavior with zero and negative values.")

    if _has_hardcoded_secret(code_lower):
        test_cases.append("Test that secrets are loaded from environment variables and never returned in logs.")

    if _has_unsafe_sql_construction(code_lower):
        test_cases.append("Test malicious SQL input such as quoted OR conditions and verify parameterized queries are used.")

    if _has_raw_card_handling(code_lower):
        test_cases.append("Test payment flow with tokenized card data and verify raw card numbers are never stored or logged.")

    if _has_unsafe_path_construction(code_lower):
        test_cases.append("Test filenames containing ../ path traversal and verify writes stay inside the allowed upload directory.")

    if (is_javascript_review and _has_js_command_injection(code_lower)) or (
        not is_javascript_review and _has_shell_command_injection(code_lower)
    ):
        test_cases.append("Test shell command inputs containing separators such as ; and && and verify they are rejected.")

    if is_javascript_review and _has_js_ssrf(code_lower):
        test_cases.append("Test webhook URLs pointing to localhost and private IP ranges and verify they are blocked.")

    if is_javascript_review and _has_js_plaintext_password_check(code_lower):
        test_cases.append("Test login with a stored password hash and verify plain-text password comparison is not used.")

    if is_javascript_review and _has_missing_express_auth(code_lower):
        test_cases.append("Test sensitive routes without a valid token and verify they return 401 or 403.")

    if is_javascript_review and _has_weak_admin_key_check(code_lower):
        test_cases.append("Test admin actions with a leaked or missing admin key and verify role-based authorization is required.")

    if is_javascript_review and _has_js_off_by_one_loop(code_lower):
        test_cases.append("Test array loops with empty and single-item arrays to verify no out-of-bounds access occurs.")

    if is_javascript_review and _has_unchecked_js_first_row(code_lower):
        test_cases.append("Test database queries that return zero rows and verify rows[0] is not read before checking length.")

    if is_javascript_review and _has_db_query_without_error_handling(code_lower):
        test_cases.append("Test database query errors and verify the route returns a controlled failure response.")

    if is_javascript_review and _has_raw_error_message_return(code_lower):
        test_cases.append("Test internal exceptions and verify detailed error messages are not returned to clients.")

    if is_javascript_review and _has_sync_file_write_in_express_route(code_lower):
        test_cases.append("Test concurrent upload or export requests and verify file writes do not block the event loop.")

    if _has_requests_post(code_lower):
        test_cases.append("Test external API timeout, connection error, and non-2xx response handling.")

    if is_javascript_review and _has_axios_post(code_lower):
        test_cases.append("Test axios timeout, network failure, and non-2xx response handling.")

    if _has_missing_transaction_rollback(code_lower):
        test_cases.append("Test a failure in the second database write and verify the full transaction is rolled back.")

    if _has_db_connection_without_close(code_lower):
        test_cases.append("Test early returns and exceptions to verify database connections are always closed.")

    if _has_datetime_json_risk(code_lower):
        test_cases.append("Test JSON export with datetime values and verify created_at is serialized as an ISO string.")

    if _has_unchecked_fetchone(code_lower):
        test_cases.append("Test a missing database row and verify the code handles fetchone returning None.")

    if _has_negative_payment_amount(code_lower):
        test_cases.append("Test negative refund and payment amounts and verify the request is rejected before processing.")

    risk_score = min(risk_score, 100)
    summary = (
        reason
        or f"Fallback review completed for {payload.language}. Local rules found {len(bugs)} issue(s) and {len(improvements)} improvement idea(s)."
    )

    return _normalize_review_response(ReviewResponse(
        summary=summary,
        risk_score=risk_score,
        bugs=bugs,
        improvements=improvements,
        test_cases=test_cases,
        fixed_code=None,
        used_ai=False,
        review_source="fallback",
    ), payload.language, payload.code)


def _review_from_ai_json(
    parsed: Dict[str, Any],
    payload: ReviewRequest | None = None,
    detected_language_override: str | None = None,
) -> ReviewResponse:
    fixed_code = parsed.get("fixed_code")
    if fixed_code is not None:
        fixed_code = str(fixed_code).strip() or None
    detected_language = detected_language_override or str(parsed.get("detected_language") or "").strip() or None
    reviewed_language = detected_language_override or str(parsed.get("reviewed_language") or detected_language or "").strip() or None
    confidence = parsed.get("language_detection_confidence")
    try:
        confidence = int(confidence) if confidence is not None else None
    except (TypeError, ValueError):
        confidence = None
    if confidence is not None:
        confidence = max(0, min(confidence, 100))

    return _normalize_review_response(ReviewResponse(
        summary=str(parsed.get("summary") or "AI review completed.").strip(),
        risk_score=_coerce_risk_score(parsed.get("risk_score", 50)),
        bugs=_bug_findings(parsed.get("bugs", [])),
        improvements=_string_list(parsed.get("improvements"), ["Review readability, edge cases, and error handling."]),
        test_cases=_string_list(parsed.get("test_cases"), ["Test the main success path and important failure paths."]),
        fixed_code=fixed_code,
        used_ai=True,
        review_source="ai",
        detected_language=detected_language,
        reviewed_language=reviewed_language,
        language_detection_source="ai" if detected_language else None,
        language_detection_confidence=confidence,
        language_detection_evidence=str(parsed.get("language_detection_evidence") or "").strip() or None,
    ), payload.language if payload else None, payload.code if payload else None)


def _extract_summary_from_ai_text(content: str) -> str | None:
    match = re.search(r'"summary"\s*:\s*"((?:\\.|[^"\\])*)"', content, re.IGNORECASE | re.DOTALL)
    if not match:
        return None

    try:
        summary = json.loads(f'"{match.group(1)}"')
    except json.JSONDecodeError:
        summary = match.group(1)

    summary = re.sub(r"\s+", " ", str(summary)).strip()
    return summary or None


def _looks_like_json_fragment(content: str) -> bool:
    stripped = content.strip()
    return stripped.startswith("{") or any(field in stripped for field in ['"summary"', '"bugs"', '"risk_score"', '"detected_language"'])


def _review_from_ai_text(content: str, payload: ReviewRequest, detected_language_override: str | None = None) -> ReviewResponse:
    cleaned = re.sub(r"\s+", " ", content.strip())
    if len(cleaned) > 500:
        cleaned = cleaned[:497].rstrip() + "..."
    summary = _extract_summary_from_ai_text(content)
    improvements = (
        ["AI response was malformed, so structured local safety findings were used instead."]
        if _looks_like_json_fragment(content)
        else ([cleaned] if cleaned else ["Review the structured findings and test ideas."])
    )

    return _normalize_review_response(ReviewResponse(
        summary=summary or "AI review completed, but the model response was not valid JSON. Structured local findings were merged with the AI response.",
        risk_score=50,
        bugs=[],
        improvements=improvements,
        test_cases=["Retest the highlighted risky paths after applying fixes."],
        fixed_code=None,
        used_ai=True,
        review_source="ai_text_repair",
        detected_language=detected_language_override,
        reviewed_language=detected_language_override,
        language_detection_source="ai" if detected_language_override else None,
        language_detection_confidence=70 if detected_language_override else None,
        language_detection_evidence="AI returned malformed JSON, so backend syntax checks repaired the language metadata.",
    ), payload.language, payload.code)


async def review_code(payload: ReviewRequest) -> ReviewResponse:
    exact_cached_review = _cached_response(payload)
    if exact_cached_review:
        return exact_cached_review

    similar_cached_review = _find_similar_cached_review(payload)
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
    base_url = os.getenv("OPENAI_BASE_URL", "").strip()
    is_ollama = _is_ollama_base_url(base_url)

    if not api_key:
        logger.info("AI review skipped because OPENAI_API_KEY is not configured.")
        fallback_review = _fallback_review(payload, "Fallback review completed because OPENAI_API_KEY is not configured.")
        _apply_similar_cache_metadata(fallback_review, similar_cached_review)
        _store_review(payload, fallback_review)
        return fallback_review

    timeout = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "90" if is_ollama else "45"))
    client_options: dict[str, Any] = {"api_key": api_key, "timeout": timeout}
    if base_url:
        client_options["base_url"] = base_url
    client = OpenAI(**client_options)
    logger.info(
        "AI review enabled. model=%s base_url=%s detected_language=%s code_lines=%s",
        model,
        "custom" if base_url else "openai-default",
        _detected_review_language(payload.language, payload.code),
        len(payload.code.splitlines()),
    )

    system_prompt = """
You are a senior software engineer and security-aware code reviewer.
Return only valid JSON.
Do not use markdown.
Always set fixed_code to null.
Do not return corrected code blocks.
Do not put raw line breaks inside JSON string values.
Detect the pasted code language yourself and return it in detected_language.
Set reviewed_language to the language you actually used for the review.
Do not copy any local/default language hint when manual language selection is not used.
Use syntax evidence for language detection: defmodule/do/end/Postgrex/Jason is Elixir;
require "sinatra" with do/end is Ruby; require "kemal"/do |env|/.as_s/Kemal.run is Crystal;
import jester/proc/routes/when isMainModule is Nim;
ns/defn/defroutes/:require/clojure.java.jdbc/ring.adapter.jetty/compojure.core is Clojure;
local function/local variables/require "resty.http"/require "cjson"/ngx.req/ngx.var/lsqlite3 is Lua/OpenResty;
open Lwt/open Opium/let bindings/|> pipelines/>>= fun/App.post/App.get is OCaml/Opium;
import groovy.*/groovy.sql.Sql/def variables/GString ${...}/command.execute()/GroovyShell is Groovy;
import vibe.d/import std.* with semicolons/HTTPServerRequest/HTTPServerResponse/URLRouter/listenHTTP/runApplication/~ string concatenation is D/vibe.d;
Object subclass:/class >> method/:= assignment/^ return/Dictionary new cascades/FileStream/OSProcess command:/ZnClient new is Smalltalk.
@import("std")/pub fn/fn ... !Type/[]const u8/std.mem.Allocator/std.process.Child.run/std.fs.cwd()/try is Zig.
stdio/stdlib + int main/printf/malloc is C; iostream/std::/templates/nullptr/cout is C++;
using System/namespace/Console.WriteLine/async Task is C#; package/import java/public class/System.out.println is Java;
fun main/data class/val/var is Kotlin; import SwiftUI/func/guard let/struct View is Swift;
package main/func/fmt.Println/err != nil is Go; fn main/let mut/println!/Result<T> is Rust;
<?php/$variables/namespace/use/function is PHP; param()/Verb-Noun cmdlets/$env: is PowerShell;
cmake_minimum_required/project/add_executable is CMake; .PHONY/targets/tab commands are Makefile;
IDENTIFICATION DIVISION/PROCEDURE DIVISION is COBOL; program/implicit none/end program is Fortran;
with Ada/procedure ... is/begin/end is Ada; program/uses/begin/end. is Pascal;
module/endmodule/always/assign is Verilog; library ieee/entity/architecture/std_logic is VHDL;
HTML tags identify HTML; CSS selectors/properties identify CSS; XML declaration/tags identify XML;
Raw JSON data has quoted keys/values with no executable imports, function/type declarations, semicolon-heavy statements, or method calls; [section] key=value can be TOML/INI depending separators.
Also recognize Haxe, V, Odin, Chapel, Pony, Ballerina, Q#, Hack, ActionScript, CoffeeScript, PureScript, ReasonML, ReScript, Fennel, Janet, Hy, MoonScript, Wren, Move, Cadence, Cairo, Circom, GLSL, HLSL, CUDA, OpenCL, ShaderLab, AWK, sed, Markdown, Mermaid, PlantUML, LaTeX, reStructuredText, Dhall, Jsonnet, Starlark, CUE, Bicep, Thrift, Cap'n Proto, YARA, Zeek, SPARQL, and XQuery by syntax fingerprints.
use strict/use warnings/my $var/sub name/Mojolicious::Lite/app->start is Perl;
pragma solidity/contract/mapping/msg.sender is Solidity; resource/provider/variable blocks are Terraform/HCL;
package:flutter/runApp/Widget build is Dart/Flutter; -module/-export/function -> clauses are Erlang;
require('express') or app.post(...) is JavaScript/Node.js.
Do not classify D/vibe.d code as JavaScript just because it uses router.post/router.get.
Do not classify Zig code as JavaScript just because it contains std.fs.cwd() or fs. syntax.
Do not classify Perl or Mojolicious code as C++ just because it uses -> method syntax.
SQL keywords inside strings are a SQL injection risk, but they do not make the whole pasted code SQL.
Do not classify Smalltalk code as SQL just because it builds SQL strings.
Do not classify Lua/OpenResty code as SQL just because it builds SQL strings.
Do not classify executable application code as JSON just because it contains quoted keys, colon separators, arrays, or map/dictionary literals.
This is defensive code review for a portfolio app. The user is asking to find and fix vulnerabilities,
not to exploit them. Do not provide executable attack steps.
Risk score must match issue severity:
- Critical issue: risk_score must be at least 80.
- High issue: risk_score must be at least 50.
- Medium issue: risk_score must be at least 30.
- Only Low issues: risk_score should usually be between 10 and 25.
If multiple Critical issues exist, risk_score should usually be 95-100.
If SQL injection appears with command injection and raw card handling, risk_score should be 100.
If SQL injection appears with payment secrets or raw card handling, risk_score should be 100.
Do not give Low risk when Critical or High issues exist.
Mark issues as Critical only for crashes, data loss, security risks, or serious runtime failures.
Mention business logic issues separately from runtime bugs.
Do not mark a Node.js/Express codebase as a language mismatch when JavaScript is selected.
If manual language selection is Auto-detect or not used, do not create a language mismatch finding.
If the selected language appears wrong, mention it only as a Low note, not a Critical bug.
If the selected language and pasted code do not match, still review the actual code shown.
Look specifically for payment/card handling, SQL injection, path traversal, missing request timeouts,
missing request exception handling, missing DB rollback, unclosed DB connections, fetchone None checks,
datetime JSON serialization, and negative refund/payment amounts when relevant.
For JavaScript/Node.js/Express, look specifically for child_process.exec command injection,
SSRF from user-controlled webhook URLs, axios timeout/error handling, fs path traversal,
hardcoded JWT/admin/database/payment secrets, plain-text password checks, missing auth middleware,
weak adminKey-only checks, off-by-one loops, rows[0] without length checks, db.query ignored errors,
raw err.message responses, and fs.writeFileSync inside routes.
child_process.exec is asynchronous; the security issue is shell command injection, not synchronous blocking.
Avoid duplicate findings. If two issues describe the same root cause, return one stronger finding.
Prefer specific test cases over generic test ideas.
Keep the response concise enough for a web UI:
- Return at most 8 strongest bug findings.
- Return at most 8 improvements.
- Return at most 8 test cases.
- Keep each explanation and suggested_fix under 80 words.
Keep fixed code practical and not overcomplicated.
Your JSON must match this structure:
{
  "summary": "short summary",
  "detected_language": "language detected from the pasted code",
  "reviewed_language": "language used for the review",
  "language_detection_confidence": 0,
  "language_detection_evidence": "brief syntax evidence used to identify the language",
  "risk_score": 0,
  "bugs": [
    {
      "title": "bug title",
      "severity": "Low/Medium/High/Critical",
      "explanation": "why this is a problem",
      "suggested_fix": "how to fix it"
    }
  ],
  "improvements": ["suggestion 1"],
  "test_cases": ["test idea 1"],
  "fixed_code": null
}
"""

    language_detection_system_prompt = """
You are a programming language detection assistant.
Return only valid JSON.
Detect the main programming language of pasted code before any review happens.
Use syntax evidence, not vulnerability type.
SQL keywords embedded inside application strings are not enough to classify the whole code as SQL.
Perl/Mojolicious syntax includes use strict, use warnings, my $variable, sub name, DBI->connect, $c->render, and app->start.
Clojure/Ring/Compojure syntax includes (ns ...), (defn ...), (defroutes ...), :require vectors, clojure.java.jdbc, jdbc/query, and run-jetty.
Lua/OpenResty syntax includes local function, local variables, require "cjson", require "lsqlite3", require "resty.http", ngx.req, ngx.var, ngx.say, cjson.decode, and cjson.encode.
OCaml/Opium syntax includes open Lwt, open Opium, let bindings, |> pipelines, >>= fun binds, `Assoc variants, and App.post/App.get routes.
Groovy syntax includes import groovy.*, groovy.sql.Sql, def variables, static methods, GString ${...}, command.execute(), and GroovyShell.
D/vibe.d syntax includes import vibe.d;, import std.*;, HTTPServerRequest/HTTPServerResponse, URLRouter, listenHTTP, runApplication, enum constants, and ~ string concatenation.
Smalltalk syntax includes Object subclass:, class >> method definitions, := assignment, ^ returns, | local variables |, Dictionary new cascades with semicolons, FileStream, OSProcess command:, ZnClient new, and message keywords ending with colon.
Zig syntax includes @import("std"), pub fn/fn, ! error unions, []const u8, std.mem.Allocator, try, std.process.Child.run, std.fs.cwd(), and .{ } struct literals.
C syntax includes stdio/stdlib includes, int main, printf, malloc/free, struct declarations, and char pointers.
C++ syntax includes iostream/vector includes, std::, using namespace std, templates, nullptr, cout, and class methods with ::.
C# syntax includes using System, namespace, Console.WriteLine, public class, async Task, and IEnumerable<T>.
Java syntax includes package/import java.*, public class, public static void main, System.out.println, and annotations.
Kotlin syntax includes fun main, data class, val/var, nullable types, companion object, and println.
Swift syntax includes import SwiftUI/Foundation, func, guard let, optional binding, and struct ... : View.
Go syntax includes package main, func declarations, fmt.Println, goroutines, :=, and err != nil checks.
Rust syntax includes fn main, let mut, println!, match, impl, Result<T>, and use std:: imports.
PHP syntax includes <?php, $variables, namespace/use, function declarations, echo, and Laravel-style code.
PowerShell syntax includes param blocks, Verb-Noun cmdlets, $env:, Write-Host, and pipeline cmdlets.
CMake syntax includes cmake_minimum_required, project(), add_executable(), target_link_libraries(), and set().
Makefile syntax includes .PHONY, targets with dependencies, tab-indented commands, variables, and $(...) expansion.
HTML/CSS/XML/TOML/INI have markup/config-specific syntax; do not classify them as JavaScript just because they contain braces or keys.
Return JSON only for raw JSON data. Do not return JSON for executable application code that has imports, functions, type declarations, semicolons, method calls, database calls, or shell/process calls.
COBOL, Fortran, Pascal, Ada, Prolog, Lisp/Scheme/Racket, MATLAB, SAS, Verilog/SystemVerilog, VHDL, Assembly, GraphQL, Protocol Buffers, Vue, Svelte, QML, Nix, GDScript, ColdFusion, ABAP, Apex, Raku, Tcl, and Elm each have local fingerprint rules in the backend; use the closest syntax match.
Also recognize Haxe, V, Odin, Chapel, Pony, Ballerina, Q#, Hack, ActionScript, CoffeeScript, PureScript, ReasonML, ReScript, Fennel, Janet, Hy, MoonScript, Wren, Move, Cadence, Cairo, Circom, GLSL, HLSL, CUDA, OpenCL, ShaderLab, AWK, sed, Markdown, Mermaid, PlantUML, LaTeX, reStructuredText, Dhall, Jsonnet, Starlark, CUE, Bicep, Thrift, Cap'n Proto, YARA, Zeek, SPARQL, and XQuery by syntax fingerprints.
Solidity syntax includes pragma solidity, contract, mapping, address, msg.sender, and public/external functions.
Terraform/HCL syntax includes resource/provider/variable blocks, terraform blocks, and var.* references.
Dart/Flutter syntax includes package:flutter imports, runApp, Widget build, StatelessWidget, and StatefulWidget.
Erlang syntax includes -module(...), -export([...]), function clauses with ->, receive, and spawn.
Do not return JavaScript for D/vibe.d code just because it uses router.post/router.get.
Do not return JavaScript for Zig code just because it contains std.fs.cwd() or fs. syntax.
Do not return C++ for Perl code just because Perl uses -> method calls.
Do not return SQL for Smalltalk code just because it builds SQL strings.
Do not return SQL for Lua/OpenResty code just because it builds SQL strings.
Do not return JSON for map/dictionary literals inside real source code.
Return SQL only for standalone SQL scripts or mostly raw SQL.
Your JSON must match this structure:
{
  "detected_language": "language name",
  "confidence": 0.0,
  "evidence": "brief syntax evidence"
}
"""

    def completion_options_for(
        user_prompt: str,
        active_system_prompt: str = system_prompt,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        options: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": active_system_prompt.strip()},
                {"role": "user", "content": user_prompt.strip()},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
        }

        if is_ollama:
            options["extra_body"] = {
                "options": {
                    "num_ctx": _int_env("OLLAMA_NUM_CTX", 512, 256, 8192),
                    "num_predict": max_tokens or _int_env("OLLAMA_NUM_PREDICT", 512, 128, 8192),
                }
            }
        elif max_tokens:
            options["max_tokens"] = max_tokens
        return options

    async def run_ai_language_detection(challenge_note: str | None = None) -> tuple[str, int | None, str | None]:
        completion = await asyncio.to_thread(
            client.chat.completions.create,
            **completion_options_for(
                _ai_language_detection_prompt(payload.code, challenge_note),
                language_detection_system_prompt,
                max_tokens=_int_env("OPENAI_LANGUAGE_DETECTION_MAX_TOKENS", 120, 40, 300),
            ),
        )

        content = completion.choices[0].message.content or "{}"
        parsed = _safe_json_loads(content)
        detected_language = _canonical_language_name(str(parsed.get("detected_language") or "")) or str(parsed.get("detected_language") or "").strip()
        if not detected_language:
            raise ValueError("AI language detection did not return detected_language.")
        confidence = parsed.get("confidence")
        try:
            confidence_float = float(confidence) if confidence is not None else None
            confidence = int(confidence_float * 100) if confidence_float is not None and confidence_float <= 1 else int(confidence_float) if confidence_float is not None else None
        except (TypeError, ValueError):
            confidence = None
        if confidence is not None:
            confidence = max(0, min(confidence, 100))
        evidence = str(parsed.get("evidence") or "").strip() or None
        return detected_language, confidence, evidence

    async def run_ai_review(user_prompt: str, detected_language: str | None = None) -> ReviewResponse:
        completion = await asyncio.to_thread(
            client.chat.completions.create,
            **completion_options_for(
                user_prompt,
                max_tokens=_int_env("OPENAI_REVIEW_MAX_TOKENS", 2200, 800, 6000),
            ),
        )

        content = completion.choices[0].message.content or "{}"
        try:
            parsed = _safe_json_loads(content)
            return _review_from_ai_json(parsed, payload, detected_language)
        except (json.JSONDecodeError, ValueError):
            return _review_from_ai_text(content, payload, detected_language)

    safety_review = _fallback_review(payload)

    try:
        syntax_detected_language, syntax_detected_confidence, syntax_detected_evidence = _syntax_language_detection(payload.code)
        ai_language_confidence: int | None = None
        ai_language_evidence: str | None = None
        try:
            ai_detected_language, ai_language_confidence, ai_language_evidence = await run_ai_language_detection()
            logger.info(
                "AI language detection completed. detected_language=%s confidence=%s evidence=%s",
                ai_detected_language,
                ai_language_confidence,
                ai_language_evidence,
            )
            challenge_note = _language_detection_challenge_note(
                ai_detected_language,
                payload.code,
                syntax_detected_language,
                syntax_detected_confidence,
                syntax_detected_evidence,
            )
            if challenge_note:
                logger.info(
                    "AI language detection challenged. detected_language=%s challenge=%s",
                    ai_detected_language,
                    challenge_note,
                )
                ai_detected_language, ai_language_confidence, ai_language_evidence = await run_ai_language_detection(
                    challenge_note
                )
                logger.info(
                    "AI language detection challenge completed. detected_language=%s confidence=%s evidence=%s",
                    ai_detected_language,
                    ai_language_confidence,
                    ai_language_evidence,
                )
        except Exception as detection_exc:
            ai_detected_language = _detected_review_language(payload.language, payload.code)
            ai_language_confidence = syntax_detected_confidence
            ai_language_evidence = syntax_detected_evidence
            logger.warning(
                "AI language detection failed; using fallback language detection. error_type=%s error=%s fallback_language=%s",
                detection_exc.__class__.__name__,
                _safe_exception_summary(detection_exc),
                ai_detected_language,
            )

        canonical_ai_detected = _canonical_language_name(ai_detected_language) or ai_detected_language
        if (
            syntax_detected_language
            and (syntax_detected_confidence or 0) >= 95
            and canonical_ai_detected != syntax_detected_language
        ):
            logger.info(
                "Strong syntax detection corrected AI language after AI detection ran. ai_language=%s syntax_language=%s confidence=%s evidence=%s",
                ai_detected_language,
                syntax_detected_language,
                syntax_detected_confidence,
                syntax_detected_evidence,
            )
            ai_detected_language = syntax_detected_language
            ai_language_confidence = syntax_detected_confidence
            ai_language_evidence = syntax_detected_evidence

        review_prompt = _ai_review_prompt(
            payload,
            ai_detected_language,
            ai_detected_language,
            similar_cached_review,
        )
        safe_review_prompt = _ai_review_prompt(
            payload,
            ai_detected_language,
            ai_detected_language,
            similar_cached_review,
            retry_safe_mode=True,
        )
        try:
            ai_review = await run_ai_review(review_prompt, ai_detected_language)
        except Exception as first_exc:
            logger.warning(
                "AI raw review attempt failed. error_type=%s error=%s",
                first_exc.__class__.__name__,
                _safe_exception_summary(first_exc),
            )
            if _should_skip_ai_retry(first_exc):
                raise
            try:
                ai_review = await run_ai_review(safe_review_prompt, ai_detected_language)
            except Exception as retry_exc:
                logger.warning(
                    "AI safe retry attempt failed. error_type=%s error=%s",
                    retry_exc.__class__.__name__,
                    _safe_exception_summary(retry_exc),
                )
                if _should_skip_ai_retry(retry_exc):
                    raise
                ai_review = await run_ai_review(_ai_local_findings_prompt(payload, safety_review), ai_detected_language)
                ai_review.review_source = "ai_from_local_findings"
        ai_review.detected_language = ai_detected_language
        ai_review.reviewed_language = ai_detected_language
        ai_review.language_detection_source = "ai"
        ai_review.language_detection_confidence = ai_language_confidence or ai_review.language_detection_confidence
        ai_review.language_detection_evidence = ai_language_evidence or ai_review.language_detection_evidence
        review = _merge_safety_checks(ai_review, safety_review, payload.language, payload.code)
        if ai_review.review_source in {"ai_from_local_findings", "ai_text_repair"}:
            review.review_source = "ai_from_local_findings"
            if ai_review.review_source == "ai_text_repair":
                review.review_source = "ai_text_repair"
        if similar_cached_review:
            review.review_source = "ai_with_cache_context"
            review.similarity_used = similar_cached_review["similarity"]
        _store_review(payload, review)
        return review
    except Exception as final_exc:
        logger.warning(
            "All AI review attempts failed; using fallback review. error_type=%s error=%s",
            final_exc.__class__.__name__,
            _safe_exception_summary(final_exc),
        )
        fallback_review = _fallback_after_ai_error(payload, similar_cached_review)
        _store_review(payload, fallback_review)
        return fallback_review
