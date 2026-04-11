# Qwen3-Reranker-8B 本地服务

本文档说明如何在本仓库里启动、关闭和调用本地 `Qwen3-Reranker-8B` 服务。

## 前提

- 机器必须是 `macOS + Apple Silicon`
- 需要 `uv`
- 首次启动会从 Hugging Face 下载 `mlx-community/Qwen3-Reranker-8B-mxfp8`，模型文件较大，请预留磁盘空间和下载时间

## 安装依赖

推荐在仓库根目录执行：

```bash
uv sync --group dev --group rerank
```

如果你只想运行服务，不跑测试，也可以只同步 rerank 依赖：

```bash
uv sync --group rerank
```

## 启动服务

默认监听 `127.0.0.1:8012`，默认模型是 `mlx-community/Qwen3-Reranker-8B-mxfp8`。

```bash
uv run --group rerank seektalent-rerank-api
```

常用参数：

```bash
uv run --group rerank seektalent-rerank-api \
  --host 127.0.0.1 \
  --port 8012 \
  --batch-size 4 \
  --model-id mlx-community/Qwen3-Reranker-8B-mxfp8 \
  --env-file .env
```

可用环境变量：

```dotenv
SEEKTALENT_RERANK_HOST=127.0.0.1
SEEKTALENT_RERANK_PORT=8012
SEEKTALENT_RERANK_MODEL_ID=mlx-community/Qwen3-Reranker-8B-mxfp8
SEEKTALENT_RERANK_BATCH_SIZE=4
SEEKTALENT_RERANK_MAX_LENGTH=8192
```

服务启动成功后会输出：

```text
SeekTalent rerank API listening on http://127.0.0.1:8012
```

## 关闭服务

前台运行时，直接按：

```text
Ctrl+C
```

如果你自己放到了后台，可以先查 PID，再执行：

```bash
kill <pid>
```

## 健康检查

请求：

```bash
curl http://127.0.0.1:8012/healthz
```

响应：

```json
{
  "status": "ok",
  "ready": true,
  "model": "mlx-community/Qwen3-Reranker-8B-mxfp8"
}
```

字段说明：

- `status`：`ok` 或 `unavailable`
- `ready`：模型是否已就绪
- `model`：当前服务加载的模型 ID

## Rerank 接口规范

接口：

```text
POST /api/rerank
```

请求头：

```text
Content-Type: application/json
```

请求体：

```json
{
  "instruction": "Given a job description, judge whether the resume is a strong match for the role.",
  "query": "Senior Python engineer with retrieval, ranking, and agent workflow experience.",
  "documents": [
    {
      "id": "resume-1",
      "text": "Candidate A resume text"
    },
    {
      "id": "resume-2",
      "text": "Candidate B resume text"
    }
  ]
}
```

字段说明：

- `instruction`：任务说明，必填，建议按你的业务场景写清楚
- `query`：查询正文，必填。在 JD 场景里通常就是 JD 全文
- `documents`：待排序文档列表，必填，至少 1 条
- `documents[].id`：文档唯一标识，必填
- `documents[].text`：文档正文，必填

成功响应：

```json
{
  "model": "mlx-community/Qwen3-Reranker-8B-mxfp8",
  "results": [
    {
      "id": "resume-2",
      "index": 1,
      "score": 0.9132,
      "rank": 1
    },
    {
      "id": "resume-1",
      "index": 0,
      "score": 0.1847,
      "rank": 2
    }
  ]
}
```

响应字段说明：

- `model`：实际参与打分的模型 ID
- `results`：排序后的结果列表
- `results[].id`：原始文档 ID
- `results[].index`：该文档在输入 `documents` 里的原始下标，从 `0` 开始
- `results[].score`：`yes` 相对 `no` 的归一化分数，范围在 `0` 到 `1` 之间
- `results[].rank`：排序名次，从 `1` 开始

排序规则：

- 先按 `score` 降序
- 分数相同则按原始输入顺序稳定排序

## 调用示例

```bash
curl -X POST http://127.0.0.1:8012/api/rerank \
  -H 'Content-Type: application/json' \
  -d '{
    "instruction": "Given a job description, judge whether the resume is a strong match for the role.",
    "query": "Senior Python engineer with retrieval, ranking, and agent workflow experience.",
    "documents": [
      {
        "id": "resume-1",
        "text": "8 years in backend systems, Python, FastAPI, Elasticsearch, and ranking pipelines."
      },
      {
        "id": "resume-2",
        "text": "5 years in frontend React development with limited backend experience."
      }
    ]
  }'
```

## 错误码

- `400`：请求体不合法，或者字段缺失、为空
- `404`：路径不存在
- `500`：推理过程异常
- `503`：模型未就绪

错误响应示例：

```json
{
  "error": "instruction must not be empty."
}
```

## 说明

- 当前服务默认只监听本机地址，不默认开放局域网访问
- 服务内部使用 Qwen 官方 reranker 的 `yes/no` 判别思路计算分数
- `instruction` 很重要，建议按你的业务场景写清楚，不要省略
