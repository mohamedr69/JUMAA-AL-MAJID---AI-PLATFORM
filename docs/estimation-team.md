# Estimation team

An administrator can create an account under **Users → New User** with the
**Estimation Engineer** role, a full name, email, and password. Email is the
sign-in username, as for existing accounts.

After signing in, estimation engineers see an Estimation Dashboard with only
**Open Project** and **Create New Project**. Project creation requires a reference
and title; client is optional. References are trimmed and uppercased, and must
be unique within estimation. Open Project searches by reference, title, or
client and opens a basic project summary.

Estimation projects are shared within the estimation team and stored separately
from design projects. Administrators can access the estimation project routes.
Design accounts retain their existing dashboard and cannot access estimation
projects. Estimation accounts cannot access design modules, including through
direct API requests. No costing or other estimation tools are enabled yet.

The startup migration adds the estimation project table and role. Existing
accounts keep their current roles; no estimation credentials are seeded.
