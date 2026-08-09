## 改了什么

<!-- 一两句话 -->

## 为什么

<!-- 这一段比上一段重要：半年后回来读代码的人（很可能是你自己）需要的正是它 -->

## 怎么验的

<!-- 跑了哪些测试？手工验了什么？涉及 AI 提示词/模型链路的改动，请说明你怎么确认输出没变差 -->

---

- [ ] `cd server && ruff check .` 通过
- [ ] `python -m pytest tests -q` 通过
- [ ] 涉及 `app/tests` 覆盖的模块时，`python -m pytest app/tests -q` 也通过
- [ ] 前端改动：`npx tsc --noEmit` 与 `npm run build` 通过
- [ ] 新增的 `open()` 都带了 `encoding="utf-8"`（中文 Windows 默认 GBK）
- [ ] 没有新增 `except Exception: pass`
- [ ] 改了响应字段的话，已确认前端消费方
