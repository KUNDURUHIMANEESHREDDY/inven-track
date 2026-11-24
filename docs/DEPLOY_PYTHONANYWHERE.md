# Deploy Inven Track on PythonAnywhere's free tier

PythonAnywhere's free Beginner plan hosts one WSGI web app on an HTTPS `username.pythonanywhere.com` domain with persistent private file storage. Unlike Render's free ephemeral filesystem, SQLite files persist over application reloads. However, free web apps **expire after one month of inactivity** and need renewal, and newer free accounts have restricted outbound network access. Confirm eligibility and account terms on the provider's official website.

## Account required

This configuration does **not** create a PythonAnywhere account or deploy the application automatically. Sign in to your authorized PythonAnywhere account to complete these steps. No API keys or passwords should be pasted into ChatGPT.

## Preparation

1. Create a free Beginner account at https://www.pythonanywhere.com/ if you are eligible, or use an existing account.
2. Upload this repository's source files to `/home/YOUR_USERNAME/inven-track`. If the GitHub repository is private, download a ZIP from your own GitHub account and upload it using the PythonAnywhere Files or Bash console.
3. Use a Python version compatible with FastAPI/Pydantic (3.11+), and create a virtual environment as supported by the hosting provider.
4. Install dependencies from `requirements.txt`, including `a2wsgi`.
5. In PythonAnywhere's **Web** tab, create a manual WSGI web app, set the working directory to `/home/YOUR_USERNAME/inven-track`, and attach the virtual environment.
6. In the generated WSGI configuration file, use:
   
   ```python
   import sys
   sys.path.insert(0, "/home/YOUR_USERNAME/inven-track")
   from pythonanywhere_wsgi import application
   ```
7. Reload the web app. Open `https://YOUR_USERNAME.pythonanywhere.com/api/health` and expect a status of `ok`.

SQLite is created at `backend/inventory.sqlite3`, within the persistent private home directory. Keep the app's source and database private from other users. Do not create a public static-file mapping that exposes database files.

## Limits & warnings

- On the free tier, the WSGI adapter supports ordinary FastAPI HTTP routes. Long-running ASGI/WebSocket workloads are not included.
- Payment and Gemini API calls might be blocked by PythonAnywhere's free outbound allowlist, so the app's optional external integrations should remain disabled unless permitted and tested.
- The free plan is for demos and learning rather than a guaranteed high-availability production app.
- Create and export encrypted database backups manually. New free accounts generally lack scheduled tasks, so do not assume daily cloud backups are automatic.
- Keep the source in a private GitHub repo when applicable; no secrets or customer databases belong in Git.
