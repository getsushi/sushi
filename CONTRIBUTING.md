# Contributing to Sushi

Thank you for helping. This guide covers how to set up, what to work on and
how to send a change.

By taking part you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Setting up

```bash
git clone https://github.com/getsushi/sushi.git
cd sushi
python -m venv .venv
```

Activate it (`.venv\Scripts\activate` on Windows, `source .venv/bin/activate`
on macOS and Linux), then:

```bash
pip install -r requirements.txt
python -m playwright install chromium
python main.py
```

Python 3.11 or later is required.

## Good first contributions

- **A new job site.** Each ATS is one small function in
  [`sushi/ats.py`](sushi/ats.py). Copy `_greenhouse`, register it in
  `read_board`, and add a test.
- **A new job feed** in [`sushi/sources.py`](sushi/sources.py).
- **A form field Sushi cannot fill.** Add a case to `tools/qa/qa_fields.py`
  first, so the failure is visible, then fix it.
- **Cover letter and resume templates.**
- Issues labelled
  [`good first issue`](https://github.com/getsushi/sushi/labels/good%20first%20issue).

For anything larger, open an issue first so the approach can be agreed before
you spend time on it.

## Making a change

1. Fork the repository and create a branch from `main`, named after the
   change (for example `ats-teamtailor-fix`).
2. Keep each pull request to one change.
3. Add or update tests in `tests/`.
4. Run the tests:

   ```bash
   python -m pytest tests
   ```

5. Open a pull request and fill in the template.

## Style

- Follow the style of the code around your change.
- Write comments that explain *why*, not *what*.
- User-facing text uses plain, short sentences.

## Privacy rules

Sushi handles people's CVs and personal details, so these are not optional:

- Never commit real CVs, real names, email addresses or phone numbers. Use the
  fictional CV in `tools/qa/` for tests.
- Never commit API keys or `.env` files.
- Personal details must not be sent to a model. Keep to the existing pattern
  where they are inserted only at the moment of typing.
- Remove personal details from logs before attaching them to an issue.

## Reporting bugs

Open an [issue](https://github.com/getsushi/sushi/issues/new/choose) and use
the bug report template. Security problems go through
[SECURITY.md](SECURITY.md), not public issues.

## Licence

By contributing, you agree that your contribution is released under the
[MIT License](LICENSE).
