# Connect a local model

The repository does not contain model weights. It connects to one locally running model through an OpenAI-compatible `POST /v1/chat/completions` endpoint.

## Compatibility contract

The current alpha expects:

1. an endpoint available only on loopback (`127.0.0.1`, `localhost`, or `::1`);
2. one configured model alias;
3. streamed and non-streamed chat completions;
4. OpenAI-style tool definitions and structured tool calls;
5. `usage.prompt_tokens` and `usage.completion_tokens` in non-streamed responses;
6. a Qwen-compatible chat template accepting `enable_thinking` and `reasoning_effort`;
7. local `tokenizer.json` and `chat_template.jinja` files for exact context accounting.

The gateway currently maps efforts to `low`, `medium`, `xhigh`, or `none`. Supporting model families with different reasoning controls is on the roadmap.

## oMLX on Apple Silicon

Install oMLX using its official Homebrew tap:

```bash
brew tap jundot/omlx https://github.com/jundot/omlx
brew install omlx
```

Place an MLX model in its own subdirectory under your model root, then start the server:

```bash
omlx serve \
  --model-dir "$HOME/models" \
  --host 127.0.0.1 \
  --port 8000 \
  --max-concurrent-requests 4
```

oMLX also provides an admin UI at `http://127.0.0.1:8000/admin`. Use it to configure the API key, model alias, context policy, memory limits, and optional SSD cache.

Verify the endpoint:

```bash
curl http://127.0.0.1:8000/v1/models \
  -H "Authorization: Bearer $LOCAL_LLM_API_KEY"
```

The model identifier returned by this endpoint must match the `model` value in `config.json`.

## Harness configuration

Create the local configuration:

```bash
cp config.example.json config.json
```

Edit these fields:

```json
{
  "upstream_url": "http://127.0.0.1:8000/v1",
  "omlx_settings": "~/.omlx/settings.json",
  "model": "your-local-model-alias",
  "model_directory": "/absolute/path/to/your/model",
  "max_concurrency": 4,
  "context_window": 65536,
  "max_output_tokens": 16384
}
```

- `upstream_url` is the OpenAI-compatible `/v1` base URL.
- `omlx_settings` is optional. When present, the Harness can read the local API key from that file.
- `model` is the API model alias.
- `model_directory` is used only for local tokenizer and chat-template accounting; weights stay outside this repository.
- `context_window` includes input, maximum output, and the configured reserve.
- `max_concurrency` is the shared limit across OpenCode and all Harness roles, not a per-role limit.

For explicit credentials, prefer an environment variable:

```bash
export LOCAL_LLM_API_KEY='your-local-api-key'
```

`OMLX_API_KEY` remains supported as a backwards-compatible alias. If no key or settings file is supplied, the client sends the placeholder bearer value `local`, which is useful only for loopback servers configured to ignore or accept it.

## Other OpenAI-compatible servers

Another server can work if it satisfies the compatibility contract above. Set:

```json
{
  "upstream_url": "http://127.0.0.1:PORT/v1",
  "omlx_settings": null,
  "model": "MODEL_ID",
  "model_directory": "/path/to/local/tokenizer-files"
}
```

Then export its local API key with `LOCAL_LLM_API_KEY`.

Do not point this alpha at a remote hosted endpoint. The configuration validator intentionally rejects non-loopback model hosts because the project is designed around local inference and may send repository code to the model.

## OpenCode

Install OpenCode using an official method, for example on macOS:

```bash
brew install anomalyco/tap/opencode
```

The launcher searches in this order:

1. `OPENCODE_BIN`;
2. `bin/opencode` for backwards compatibility;
3. `opencode` on `PATH`.

No OpenCode binary is committed to this repository.

## Validation

```bash
./scripts/qwen-harness doctor
./scripts/qwen-harness start
```

`doctor` checks the Harness, the upstream model server, model name, context limit, output limit, and OpenCode installation.

## Security

- Keep the inference endpoint on loopback unless you have configured authentication and understand the exposure.
- Never commit `config.json`, `.env`, API keys, settings files, or model weights.
- Treat `chat_template.jinja` as executable template logic and source it from a model repository you trust.
- A local endpoint does not automatically make untrusted generated code safe; verification still runs project code.

Official references: [oMLX](https://github.com/jundot/omlx), [OpenCode installation](https://opencode.ai/docs).
