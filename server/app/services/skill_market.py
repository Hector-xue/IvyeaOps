"""能力市场客户端：从门道社区装 Skill 的**守门人**。

`data/skills` 下有 63 个可执行脚本 —— Skill 不是文档，是能跑的代码，而且跑在
用户自己的机器上、能触发子进程、能连领星。**让陌生人的代码一键进来，就是一条完整
的供应链攻击通道。** 门道那边把审核做得再好，客户端不把关也没用：源是可以换的
（用户可以指向自建镜像），最终决定"要不要让这段代码落到我机器上"的是这里。

四条不可动摇的约束（对应优化方案 6.2.1）
----------------------------------------
* **C1 v1 只装 A 类**（纯 ``SKILL.md``、不带任何可执行文件）。B 类要等沙箱执行
  做完再开。这条不是保守，是顺序问题：没有沙箱就开 B 类，等于把选择权推给
  没有信息判断的用户。
* **C2 纯只读、零上报、可关闭、可换源。** 不带机器码、不带店铺信息、不回传安装
  统计。装过的 skill 落本地，断网照常用。开源用户对外联极度敏感 ——
  第一个 issue 就会是"为什么我的实例在往外发请求"。
* **C3** 市场是门道的独立分区，不混进沉淀区（那边的判据是"换个行业也能拿走"，
  而 skill 大量是亚马逊专用）。这条在门道侧落实。
* **C4 先播种再开放**：空货架比没有货架更伤。

安装前必须让用户看见什么
------------------------
``analyze()`` 产出一份**能力清单**：这个 skill 会执行什么、访问哪些网络、读写哪些
路径。清单由静态分析生成，作者删不掉。UI 把它渲染成人话，用户点确认才装 ——
"我不知道它要干什么就装了"这件事，不该发生。
"""
from __future__ import annotations

import ast
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger("ivyea.services.skill_market")

DEFAULT_MARKET_URL = "https://mendao.ivyea.com/api/market"

_EXECUTABLE_SUFFIXES = {".py", ".sh", ".bash", ".zsh", ".js", ".mjs", ".cjs", ".ts",
                        ".rb", ".pl", ".php", ".ps1", ".bat", ".cmd", ".exe", ".bin"}
_MAX_TOTAL_BYTES = 2 * 1024 * 1024
_MAX_FILES = 200

# —— A 类（纯提示词）的风险特征 ——
# 提示词注入：让 skill 在别人的会话里改写系统指令、套取系统提示词。
_PROMPT_INJECTION = re.compile(
    r"(忽略(以上|之前|前面).{0,6}(指令|要求|设定)"
    r"|ignore\s+(all\s+)?(previous|above|prior)\s+instructions"
    r"|(输出|告诉我|展示).{0,8}(你的)?(系统提示词|system\s*prompt)"
    r"|disregard\s+your\s+(guidelines|instructions)"
    r"|你现在是.{0,12}(开发者模式|no\s*restrictions))",
    re.IGNORECASE,
)
# 套密钥：诱导用户把凭据贴进对话，或让 agent 去读凭据文件。
_CREDENTIAL_BAIT = re.compile(
    r"((把|请).{0,10}(api\s*key|密钥|token|口令).{0,10}(贴|发|告诉|输入))"
    r"|(读取|打开|cat).{0,10}(\.env|\.master\.key|hub_settings\.json|\.ssh)"
    r"|IVYEA_OPS_SECRET|IVYEA_OPS_PASSWORD_HASH",
    re.IGNORECASE,
)
# 外链诱导：把用户/agent 引到站外去执行或提交东西。
_EXFIL_HINT = re.compile(
    r"(curl|wget|Invoke-WebRequest)\s+[^\s]*https?://"
    r"|https?://[^\s]{0,80}/(collect|report|upload|exfil|beacon)",
    re.IGNORECASE,
)

# —— B 类（带脚本）的 AST 特征 ——
_DANGEROUS_CALLS = {"eval", "exec", "compile", "__import__", "system", "popen"}
# 分两档，而不是笼统一档。市场里的 skill 没有任何正当理由直接起子进程或开裸
# socket —— agent 本身有受控的工具做这些事，绕过它就是想避开审计和权限。
_BLOCKED_MODULES = {"subprocess", "socket", "ctypes", "pty", "telnetlib",
                    "multiprocessing", "http.client", "ftplib", "smtplib"}
# 这些是正当用途居多、但值得让用户看见的。一律判 block 会让扫描器变成"狼来了"。
_WARN_MODULES = {"shutil", "tempfile", "urllib", "requests", "httpx"}
_SENSITIVE_PATHS = re.compile(
    r"(\.env\b|\.master\.key|hub_settings\.json|\.ssh/|id_rsa|/etc/(passwd|shadow)"
    r"|\.aws/credentials|\.git-credentials)")
# 硬编码凭据：前缀明确的那些，宁可少报也不误伤（把随便一串长字符串当密钥
# 会让扫描器变成"狼来了"，很快就没人看它的结论了）。
_HARDCODED_SECRET = re.compile(
    r"(sk-[A-Za-z0-9_\-]{16,}|gh[pousr]_[A-Za-z0-9]{16,}|xox[baprs]-[A-Za-z0-9\-]{10,}"
    r"|AKIA[0-9A-Z]{16})")


@dataclass
class Capability:
    """一条能力声明 —— UI 上要渲染成人话给用户看。"""

    kind: str                 # command / network / filesystem / credential / prompt
    detail: str
    severity: str = "info"    # info / warn / block
    where: str = ""


@dataclass
class Manifest:
    skill_class: str = "A"            # A=纯 SKILL.md，B=含可执行文件
    files: List[str] = field(default_factory=list)
    total_bytes: int = 0
    capabilities: List[Capability] = field(default_factory=list)
    blockers: List[str] = field(default_factory=list)

    @property
    def installable(self) -> bool:
        return not self.blockers

    def to_dict(self) -> Dict[str, Any]:
        return {
            "class": self.skill_class,
            "files": self.files,
            "total_bytes": self.total_bytes,
            "installable": self.installable,
            "blockers": self.blockers,
            "capabilities": [c.__dict__ for c in self.capabilities],
            "human_summary": human_summary(self),
        }


def human_summary(manifest: Manifest) -> str:
    """把能力清单说成人话。UI 直接渲染这一段。

    刻意也写"不会做什么" —— 只列风险项的话，用户无从判断"没列出来"是安全
    还是没扫到。
    """
    if manifest.blockers:
        return "这个 Skill 不能安装：" + "；".join(manifest.blockers)

    doing = [c.detail for c in manifest.capabilities if c.severity != "info"]
    if manifest.skill_class == "A" and not doing:
        return ("这个 Skill 只有提示词，没有任何可执行文件 —— "
                "不会执行命令、不会访问网络、不会读写你的文件。")
    if not doing:
        return "静态分析没有发现风险行为。"
    return "注意：这个 Skill 会 " + "；".join(doing)


# ── 分析 ────────────────────────────────────────────────────────────────

def _scan_prompt(text: str, where: str) -> List[Capability]:
    caps: List[Capability] = []
    if _PROMPT_INJECTION.search(text):
        caps.append(Capability("prompt", "试图覆盖或套取系统指令（提示词注入）",
                               "block", where))
    if _CREDENTIAL_BAIT.search(text):
        caps.append(Capability("credential", "索取密钥，或引导读取凭据文件",
                               "block", where))
    if _EXFIL_HINT.search(text):
        caps.append(Capability("network", "引导向站外地址提交内容", "warn", where))
    return caps


def _scan_python(source: str, where: str) -> List[Capability]:
    """AST 级扫描。用 AST 而不是正则：正则会被 ``__im''+''port__`` 这类拼接绕过，
    而 AST 看的是真实语义。解析不了的（语法错误）直接判定为可疑 ——
    一个语法都不对的脚本本来也不该装。"""
    caps: List[Capability] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return [Capability("command", "脚本无法解析（语法错误），无法判断其行为",
                           "block", where)]

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""])
            for name in names:
                root = (name or "").split(".")[0]
                full = (name or "")
                if root in _BLOCKED_MODULES or full in _BLOCKED_MODULES:
                    caps.append(Capability(
                        "command",
                        f"导入 {full or root}（可直接起子进程或建立网络连接，"
                        "绕过 agent 的受控工具与审计）",
                        "block", where))
                elif root in _WARN_MODULES:
                    caps.append(Capability(
                        "network" if root in {"urllib", "requests", "httpx"} else "filesystem",
                        f"导入 {root}", "warn", where))
        elif isinstance(node, ast.Call):
            fn = node.func
            fname = getattr(fn, "id", None) or getattr(fn, "attr", None)
            if fname in _DANGEROUS_CALLS:
                caps.append(Capability("command", f"调用 {fname}()（可执行任意代码）",
                                       "block", where))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _SENSITIVE_PATHS.search(node.value):
                caps.append(Capability("filesystem", f"引用敏感路径 {node.value[:60]}",
                                       "block", where))
            elif _HARDCODED_SECRET.search(node.value):
                caps.append(Capability("credential", "脚本里硬编码了凭据", "warn", where))
    return caps


def analyze(files: Dict[str, bytes], *, allow_class_b: bool = False) -> Manifest:
    """静态分析一个 skill 包，产出能力清单。

    ``files`` 是 ``{包内相对路径: 内容}``。
    """
    manifest = Manifest(files=sorted(files), total_bytes=sum(len(b) for b in files.values()))

    if len(files) > _MAX_FILES:
        manifest.blockers.append(f"文件数超过上限（{len(files)} > {_MAX_FILES}）")
    if manifest.total_bytes > _MAX_TOTAL_BYTES:
        manifest.blockers.append("体积超过 2MB 上限")
    if not any(Path(n).name == "SKILL.md" for n in files):
        manifest.blockers.append("缺少 SKILL.md，不是一个合法的 Skill")

    executables = [n for n in files if Path(n).suffix.lower() in _EXECUTABLE_SUFFIXES]
    if executables:
        manifest.skill_class = "B"
        manifest.capabilities.append(
            Capability("command", f"包含 {len(executables)} 个可执行脚本", "warn",
                       ", ".join(executables[:5])))
        if not allow_class_b:
            # C1：没有沙箱之前不开 B 类。把选择权推给没有信息判断的用户，
            # 是最坏的一种"尊重用户"。
            manifest.blockers.append(
                "含可执行脚本（B 类）。当前版本只支持纯提示词的 A 类 Skill —— "
                "沙箱执行做好之前不开放，以免陌生代码直接在你机器上运行")

    for name, blob in files.items():
        try:
            text = blob.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            continue
        if Path(name).suffix.lower() in {".md", ".txt", ".yaml", ".yml", ".json"}:
            manifest.capabilities.extend(_scan_prompt(text, name))
        elif Path(name).suffix.lower() == ".py":
            manifest.capabilities.extend(_scan_python(text, name))
        else:
            manifest.capabilities.extend(_scan_prompt(text, name))

    for cap in manifest.capabilities:
        if cap.severity == "block":
            manifest.blockers.append(f"{cap.detail}（{cap.where}）")

    return manifest


# ── 完整性校验 ──────────────────────────────────────────────────────────

def verify_payload(blob: bytes, *, sha256: str = "", signature: str = "",
                   public_key: str = "") -> List[str]:
    """校验下载内容。返回问题列表，空列表表示通过。

    签名的意义在"可换源"这件事上：用户可以把市场指向自建镜像，
    校验和只能证明"没传坏"，签名才能证明"是那边发布的那份"。
    """
    problems: List[str] = []
    if sha256:
        actual = hashlib.sha256(blob).hexdigest()
        if actual != sha256.lower():
            problems.append(f"校验和不符（期望 {sha256[:12]}… 实际 {actual[:12]}…）")
    if public_key and signature:
        try:
            import base64

            from cryptography.exceptions import InvalidSignature
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

            key = Ed25519PublicKey.from_public_bytes(base64.urlsafe_b64decode(public_key))
            key.verify(base64.urlsafe_b64decode(signature), blob)
        except InvalidSignature:
            problems.append("签名无效 —— 这份内容不是市场发布的那一份")
        except Exception as exc:  # noqa: BLE001
            problems.append(f"签名校验失败：{type(exc).__name__}")
    elif signature and not public_key:
        problems.append("带了签名但本地没有配置公钥，无法验证")
    return problems


# ── 本地安装账本 ────────────────────────────────────────────────────────

def _ledger_path() -> Path:
    from app.core.config import settings
    return Path(settings.data_dir) / "installed_skills.json"


def installed() -> Dict[str, Any]:
    """已安装的社区 skill：来源、版本、校验和。

    记来源和 sha256 是为了两件事：升级时能算 diff；将来某个 skill 被市场下架
    （比如发现是恶意的），能立刻查出本机装没装。
    """
    path = _ledger_path()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("安装账本读不了，按空处理：%s", path)
        return {}


def record_install(slug: str, *, version: str, sha256: str, source: str,
                   skill_class: str) -> None:
    import time

    ledger = installed()
    ledger[slug] = {"version": version, "sha256": sha256, "source": source,
                    "class": skill_class, "installed_at": int(time.time())}
    path = _ledger_path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)

    from app.core import audit
    audit.record("skill_market", "install", target=f"{slug}@{version}",
                 detail={"source": source, "class": skill_class, "sha256": sha256[:16]})


def forget(slug: str) -> bool:
    ledger = installed()
    if slug not in ledger:
        return False
    ledger.pop(slug)
    path = _ledger_path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    from app.core import audit
    audit.record("skill_market", "uninstall", target=slug)
    return True


def market_url() -> str:
    from app.core import hub_settings
    return str(hub_settings.get("skill_market_url") or DEFAULT_MARKET_URL).rstrip("/")


def market_enabled() -> bool:
    """默认**关闭**。

    这是个会往外发请求的功能，而这个产品的卖点是"数据不出本机"。
    默认开会让用户在不知情的情况下产生外联 —— 哪怕请求本身完全匿名，
    这个信任成本也不该由我们替他付。
    """
    from app.core import hub_settings
    value = hub_settings.get("skill_market_enabled")
    return bool(value) if isinstance(value, bool) else False
