# Contributing

Welcome! This guide gets you from a fresh clone to your first pull request.

## Set up

You need Python 3.9+, Node.js 18+ and git.

1. Fork `kvdsn/pfm` on GitHub and clone your fork.
2. Install everything:

   ```sh
   python3 -m venv .venv
   .venv/bin/pip install -r requirements-dev.txt
   cd frontend && npm install && npm run build && cd ..
   ```

3. Build the demo database. It has made-up accounts, about two years of transactions, categories, rules and forecast settings for Personal, Business and Kids:

   ```sh
   PYTHONPATH=backend .venv/bin/python -m pfm.demo
   ```

4. Start the app on it and open `http://127.0.0.1:8000`:

   ```sh
   PFM_HOME=data/demo PYTHONPATH=backend .venv/bin/python -m pfm
   ```

To start over, delete `data/demo` and build it again. The demo data is generated from the current date, so it always runs up to today.

## Work on a change

- **Start from a fresh branch** in your fork for each change: `git checkout -b my-change`.
- **Frontend:** for a quick feedback loop, keep the app running and run `cd frontend && npm run dev` in a second terminal. Open the address it prints; changes then reload instantly.
- **Backend:** after a backend change, stop the app and start it again.
- **Before you open a pull request,** run the checks:

  ```sh
  .venv/bin/python -m unittest discover -s tests
  cd frontend && npx tsc --noEmit -p . && npm test
  ```
- **Add tests** for what you change: backend tests go in `tests/`, and frontend tests sit next to their component as `*.test.tsx`.
- **Update the docs** when behavior changes: README.md, `docs/REQUIREMENTS.md` or `docs/CATEGORIZATION_RULES.md`.
- **Open a pull request** to `kvdsn/pfm` with a short description of what changed and why, plus a screenshot for visual changes.

## Find your way around

- **README.md** explains what the app does. `docs/REQUIREMENTS.md` explains how it behaves in detail.
- **AGENTS.md** has a code map and the rules the code must keep. It's written for AI coding assistants, but it's the quickest tour for people too.
- **Backend:** `backend/pfm`, with FastAPI endpoints in `server.py` and the logic in `core/`.
- **Frontend:** `frontend/src`, in React and TypeScript.

## Rules

- **Never use real financial data**, whether yours or anyone else's, in code, tests, screenshots or issues. Use the demo data and made-up values.
- **Keep it local:** no external services, analytics or network requests.
- **Keep Personal, Business and Kids separate.**
- **Match the style of the surrounding code**, and keep changes focused: one feature or fix per pull request.

## Ideas to start with

Smaller ideas:

- **Export the listed transactions as CSV**, from the transactions table.
- **Support another bank's CSV export**, such as ING or Argenta. Start from `core/parsing.py` and its tests.
- **Add a card statement to the demo data**, so the card features show up too (`backend/pfm/demo.py`).

Bigger ones are in [docs/BACKLOG.md](docs/BACKLOG.md).
