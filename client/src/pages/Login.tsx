import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { login, register } from "../api/client";
import { errText } from "../lib/errText";

type Mode = "login" | "register";

export default function Login() {
  const [mode, setMode] = useState<Mode>("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const navigate = useNavigate();

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setNotice(null);
    try {
      if (mode === "login") {
        await login(username.trim(), password);
        navigate("/");
      } else {
        const r = await register(username.trim(), password);
        setNotice(r.message || "注册成功，待管理员审批");
        setMode("login");
        setPassword("");
      }
    } catch (err: any) {
      setError(errText(err, mode === "login" ? "登录失败" : "注册失败"));
    } finally {
      setLoading(false);
    }
  };

  const isReg = mode === "register";

  return (
    <div className="login-wrap">
      <form className="login-box" onSubmit={onSubmit}>
        <div className="mark">OPS WORKBENCH</div>
        <h1>{isReg ? <>注册账号 · <b>IvyeaOps</b></> : <>欢迎回来 · <b>IvyeaOps</b></>}</h1>

        <label>{isReg ? "邮箱" : "账号 / 邮箱"}</label>
        <input
          className="inp"
          autoFocus
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          placeholder={isReg ? "you@company.com" : "管理员账号或注册邮箱"}
          autoComplete={isReg ? "email" : "username"}
        />

        <label>密码{isReg && "（至少 8 位）"}</label>
        <input
          className="inp"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete={isReg ? "new-password" : "current-password"}
        />

        {error && <div className="err">✗ {error}</div>}
        {notice && <div className="err" style={{ color: "var(--acc)" }}>✓ {notice}</div>}

        <button type="submit" disabled={loading || !username.trim() || !password}>
          {loading ? (
            <><span className="spin" style={{ marginRight: 8 }} />{isReg ? "提交注册..." : "SIGNING IN..."}</>
          ) : (
            isReg ? "→ 注册" : "→ SIGN IN"
          )}
        </button>

        <div style={{ marginTop: 14, fontSize: 12, color: "var(--t3)", textAlign: "center" }}>
          {isReg ? (
            <>已有账号？<a onClick={() => { setMode("login"); setError(null); }} style={{ color: "var(--acc)", cursor: "pointer" }}>去登录</a></>
          ) : (
            <>没有账号？<a onClick={() => { setMode("register"); setError(null); setNotice(null); }} style={{ color: "var(--acc)", cursor: "pointer" }}>注册</a>（注册后需管理员审批）</>
          )}
        </div>
      </form>
    </div>
  );
}
