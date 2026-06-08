# Product Goal

## Problem

SeekTalent currently starts from structured manual input: `jobTitle`, `jdText`, `notes`, and `sourceKinds`. That is too rigid for the desired local Workbench experience. The user wants to describe the hiring need in natural language, have the system clarify and confirm the requirement, and then continue through the existing Workbench/runtime flow.

## Target Experience

The first screen for a new hiring run becomes a general conversation window.

The user can write naturally, for example:

```text
帮我找一个做实时数据平台的高级后端，最好有 Flink、Python、检索系统经验，人在上海或杭州。
公司是 AI infra 方向，候选人要能独立做架构，不要只会写业务接口。
```

The system responds with a requirement confirmation instead of immediately starting the search:

```text
我理解这次招聘目标是：

岗位：高级后端/实时数据平台工程师
核心要求：Flink 或实时计算经验、Python 后端、检索或排序系统经验、能独立做架构
地域：上海或杭州优先
排除：只做普通业务接口、缺少系统设计经验
检索渠道：CTS + 猎聘

请确认是否按这个需求创建 Workbench 会话。
```

After confirmation, the harness creates the Workbench session and starts the existing requirement preparation workflow. The user still reviews and approves the requirement sheet before the runtime starts sourcing. Once execution starts, the initial version lets the user ask the conversation agent about progress and results; it does not mutate an in-progress runtime workflow.

## Non-MVP Standard

This is not a prototype. The implementation must include:

- real local persistence;
- deterministic state transitions;
- named error codes;
- project-isolated Codex configuration;
- project-isolated Codex memory;
- provider smoke checks;
- backend tests;
- frontend tests;
- Workbench integration tests;
- clear user-visible failure states;
- local verification commands.

## Local-Only Product Positioning

This is a local SeekTalent product feature. It must not be described, implemented, configured, or verified as a SaaS workflow.

Local means:

- the Workbench runs on the user's machine;
- project data lives under this project or SeekTalent's local data root;
- no hosted multi-tenant control plane is introduced;
- no cloud database is introduced;
- no server-side account system beyond the existing local Workbench auth is introduced;
- OpenAI API is not the default model path.

## Commercial Product Posture

SeekTalent may be packaged as a commercial product while internally invoking Codex App Server locally.

That commercial posture is acceptable only if the implementation keeps these lines separate:

- SeekTalent product code and local Workbench state;
- Codex App Server artifacts used as third-party components;
- project-isolated `CODEX_HOME`;
- project-isolated Codex memory;
- user/global Codex auth and user/global `~/.codex`.

If any Codex CLI, App Server, binary, source code, or other Codex artifact is redistributed with SeekTalent, the packaged product must retain the applicable Apache-2.0 license notice for that Codex artifact. If SeekTalent only invokes an operator-installed Codex binary and does not redistribute Codex artifacts, the implementation must document that packaging choice explicitly.

## Success Outcome

The final product outcome is:

1. A user can open the local Workbench new-session screen.
2. The user can type a natural-language hiring need.
3. The system can ask clarifying questions when the input is underspecified.
4. The system can produce a structured confirmation with job title, JD text, notes, and source kinds.
5. The user can edit or reject the confirmation.
6. The user can confirm the requirement.
7. The harness creates a Workbench session through the existing Workbench boundary.
8. The existing requirement preparation flow starts.
9. The existing runtime remains responsible for sourcing after requirement approval.
10. During the initial version, the conversation agent can start the workflow and read workflow progress/results, but cannot change in-progress runtime state.

## Design Principle

Use Codex App Server as the local intake harness and memory engine. Do not rebuild a general agent harness and do not use the Codex SDK for this feature. The SeekTalent code should own only:

- local configuration isolation;
- structured intake contracts;
- state persistence;
- Workbench bridging;
- UI;
- verification and error handling.

## UI Timing Principle

The UI design will likely be redesigned after designer input. That should not block the bottom-layer implementation.

The current implementation should therefore provide a functional transcript-style interface with stable API contracts and minimal visual commitment. A later UI redesign should be able to replace the component layout and interaction details while reusing the same backend state machine, conversation APIs, and Workbench handoff contract.
