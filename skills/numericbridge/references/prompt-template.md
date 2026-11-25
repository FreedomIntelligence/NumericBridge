# NumericBridge request template / 通用任务模板

A plain-language request is enough. State the outcome you want and what to work
on; omit anything unknown. The agent follows [request confirmation](request.md)
and the [workflow](workflow.md) rather than asking you to design the experiment.

## 中文（复制给新会话）

```text
请用 NumericBridge 帮我【要解决的问题、要比较的对象，或希望交付的结果】。
已有材料（可选）：【案例目录、交接记录、运行命令或证据链接】。
限制（可选）：【可用环境、预算、验收要求、不要动的任务或文件】。

新任务请先确认目标和必要授权，再实际执行；已有案例请从已确认的范围和检查点继续，先查现有任务，不重复启动。能从材料查到的信息不用再问我。交付结论、证据和未完成项；未经我确认，不投稿、推送或开 PR。
```

## English

```text
Use NumericBridge to help me [solve this problem, compare these targets, or deliver this result].
Existing material (optional): [case directory, handoff, run command, or evidence link].
Constraints (optional): [allowed environment, budget, acceptance criteria, or work to leave untouched].

For a new task, confirm the goal and necessary authorization, then execute. For an existing case, resume within its confirmed scope from the checkpoint and check for active jobs before starting anything. Look up what you can from the supplied material. Deliver conclusions, evidence, and open items. Do not submit, push, or open a PR without my confirmation.
```

For a new case, mention the hardware, model, framework, and workload if known.
For a continuation, a case directory or handoff plus the desired outcome is
usually enough. Experiment design, controls, and acceptance details belong in
the workflow, not in the request template.
