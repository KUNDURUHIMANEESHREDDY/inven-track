# Render free deployment — DEMO ONLY

The `render.yaml` file is an explicitly non-persistent preview environment.
Render's Free Web Service filesystem is ephemeral. SQLite users, invoices, and inventory
created in the hosted demo can be deleted on idle spin-down, restart, or redeploy.
The application therefore disables owner signup/login and real payments when
`INVENTRACK_DEMO_ONLY=1`. Users can click **Demo Login** and experiment with sample
records; these records are temporary. Never enter real customer or business data.

For Render deployment, connect a Render account you are eligible to use and authorize access
to the private GitHub repository. Then create a Blueprint from `render.yaml`. Ensure that
the service is on the **free** tier. The Render-hosted site will get an HTTPS `.onrender.com`
address. Free apps can sleep when idle and may take time to wake.

For persistent inventory and billing data, use the PythonAnywhere WSGI deployment in
`DEPLOY_PYTHONANYWHERE.md` or a host with a genuinely persistent database.
