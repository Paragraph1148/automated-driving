# viewer

The Mission Control front end: Vite + React + TypeScript, built to a single
inlined HTML file that replaces `sarathi/assets/mission_control.html`.

```bash
npm install
npm run build        # -> ../sarathi/assets/mission_control.html
```

## Watch out: the server reads the page once

`serve.py` builds its HTTP routes from `_live_page()` **at startup**, so the
page is read from disk exactly once when the process boots. Rebuilding the
viewer while `sarathi serve` is running changes nothing in the browser — you
will be testing the previous build and wondering why your change did not take.

Restart the server after every build:

```bash
npm run build && (pkill -f 'sarathi serve'; uv run sarathi serve)
```

## Dev loop with hot reload

For front-end work, run the Python server and Vite side by side. Vite proxies
the telemetry socket to the real simulator, so you get HMR against live data:

```bash
uv run sarathi serve            # terminal 1, port 8420
npm run dev                     # terminal 2, port 5173 -> proxies /ws to 8420
```

Open <http://localhost:5173>. `__RUN_DATA__` is not substituted there, so the
page falls back to live mode, which is what you want anyway.
