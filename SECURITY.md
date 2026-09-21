# Security policy

## Supported version

The current `main` branch is an alpha and receives security fixes. No stable release line exists yet.

## Reporting a vulnerability

Until a public security contact is configured, open a GitHub private vulnerability report after the repository is published. Do not include credentials, private source code, or exploit payloads in a public issue.

## Threat model

The Harness assumes:

- the local operator controls the target repository;
- the local model server and model files are trusted;
- configured verification commands may execute repository code;
- model output is untrusted until verification and review complete.

Protections include loopback-only service validation, authenticated Harness endpoints, scoped file tools, excluded secrets, isolated snapshots, network-denied macOS checks, candidate hashes, source-conflict detection, and apply backups.

## Non-goals and limitations

- `sandbox-exec` is not VM-grade isolation.
- There is no hard per-process memory ceiling.
- A malicious build tool, compiler, interpreter, model file, or chat template may exploit vulnerabilities outside the Harness.
- Allowing broad `read_roots` can expose sensitive data to tests or model-visible logs.
- Exposing oMLX or the Harness beyond localhost requires separate network authentication, transport security, and access control.

Run unknown or adversarial repositories inside a disposable virtual machine with no personal credentials.

## Secret handling

Never commit:

- `config.json` when it contains private paths or settings;
- `.env*`, `*.key`, or `*.pem`;
- oMLX settings or Harness `.state`;
- raw model logs containing private project code;
- model weights.

Use `LOCAL_LLM_API_KEY` for an authenticated local inference endpoint.
