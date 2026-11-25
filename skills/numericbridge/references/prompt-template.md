# NumericBridge request template / 通用任务模板

A plain-language request is enough. State the outcome you want and what to work
on; omit anything unknown. The agent follows [request confirmation](request.md)
and the [workflow](workflow.md) rather than asking you to design the experiment.

## 中文（复制给新会话）

```text
请用 NumericBridge 帮我【要解决的问题、要比较的对象，或希望交付的结果】。
已有材料（可选）：【案例目录、交接记录、运行命令或证据链接】。
限制（可选）：【可用环境、预算、验收要求、不要动的任务或文件】。

按 NumericBridge 工作流持续执行，不要只给计划或在进度更新后停下。若这是跨多轮的任务且支持 Goal 模式，请创建或恢复该任务的 Goal，持续推进到完成，或明确说明为什么无法继续。
```

## English

```text
Use NumericBridge to help me [solve this problem, compare these targets, or deliver this result].
Existing material (optional): [case directory, handoff, run command, or evidence link].
Constraints (optional): [allowed environment, budget, acceptance criteria, or work to leave untouched].

Follow the NumericBridge workflow through execution. Do not stop at a plan or progress update. If this is a multi-round task and Goal mode is available, create or resume its Goal and keep working until the task is done or you can explain why it cannot continue.
```

For a new case, mention the hardware, model, framework, and workload if known.
For a continuation, a case directory or handoff plus the desired outcome is
usually enough. Experiment design, controls, and acceptance details belong in
the workflow, not in the request template.
