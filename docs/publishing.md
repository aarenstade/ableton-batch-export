# Put this repository on GitHub

The code is ready for a personal GitHub repository; no remote is configured and
this project does not publish anything automatically.

1. Review `README.md`, `LICENSE`, `THIRD_PARTY.md` and the files shown by `git status`.
2. Keep the code separate from personal inputs. `.gitignore` excludes local
   configurations, databases, logs, generated reports, media and receipts.
3. Run `python -m unittest discover -s tests -v` in the installed virtual environment.
4. Create an empty GitHub repository, then run these commands in this folder:

   ```sh
   # Run this only if the folder is not already initialized:
   git init -b main
   git add .
   git diff --cached --stat
   git commit -m "Initial public version of Ableton Batch Export"
   git remote add origin https://github.com/YOUR-USERNAME/ableton-batch-export.git
   git push -u origin main
   ```

Replace `YOUR-USERNAME` with your account. Do not add the old batch workspace or
any mounted music library as this repository's parent content. Only publish this
single extracted folder. The included GitHub Actions workflow runs offline tests
and builds the package; it never attempts to start Live.
