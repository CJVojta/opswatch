# OpsWatch

OpsWatch monitors HTTP services and turns individual health checks into an incident timeline. The dashboard shows current status, successful checks over the last 24 hours, response times, and recent failures.

## Run the demo

With Python 3.10 or newer, run this from the repository root:

```sh
python app.py --demo
```

Open [127.0.0.1:8080](http://127.0.0.1:8080). The demo loads 384 simulated checks across four services. Payments API has an open incident, while Customer Portal and Account Service show resolved incidents. Select **Check history** to see the underlying samples. The demo uses a temporary database and sends no probes.

## Monitor a real endpoint

```sh
python app.py
```

Choose **Add monitor**, enter a direct HTTP(S) health URL, and set the check interval and timeout. A 2xx response passes. Redirects and network failures count as failed checks. The dashboard refreshes every five seconds.

## How it works

- The scheduler runs up to four concurrent HTTP checks and allows one in-flight check per monitor.
- Two consecutive failures open an incident. Two consecutive passes resolve it. A monitor with no prior status becomes operational after its first passing check.
- SQLite stores the monitor state, raw samples, and incident history. Each check and its state change commit together. A partial unique index enforces one open incident per monitor.
- Check samples are retained for 30 days. The 24-hour uptime card reports the percentage of successful samples, and the latency card averages successful samples only.
- The server binds to loopback and is intended for a single local operator. Use `--port` and `--db` to select a different port or database file.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/overview` | Monitor cards, 24-hour metrics, and incident timeline |
| GET | `/api/monitors/{id}/checks` | Latest 100 checks |
| POST | `/api/monitors` | Add a monitor with JSON fields `name`, `url`, `interval`, and `timeout` |
| DELETE | `/api/monitors/{id}` | Remove a monitor and its history |

## Tests

```sh
python -m unittest discover -s tests -v
```
