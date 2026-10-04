# CI/CD Workflow Documentation

## Build Backend Docker Image

Workflow: `.github/workflows/deploy.yml`

### What it does:
1. **Build Docker image** on every push to `main`
2. **Push to GHCR** (GitHub Container Registry)
3. ✅ That's it! Manual deployment to Railway from there

### GitHub Actions Workflow

Automatically triggers on push to `main`:
```bash
git add .
git commit -m "feat: something"
git push origin main
```

Image will be available at: `ghcr.io/incodiy/ehos-backend-api:main`

Check workflow status: https://github.com/incodiy/ehos-backend-api/actions

---

## Manual Railway Deployment (Local)

If you want to deploy directly to Railway from your machine:

### Prerequisites:
1. **Install Railway CLI:**
   ```bash
   npm install -g @railway/cli
   ```

2. **Login to Railway:**
   ```bash
   railway login
   ```

3. **Link to project:**
   ```bash
   railway link
   # Select ehos-backend-api project
   ```

### Deploy:
```bash
railway up
# Selects backend service or prompts to choose
```

### Run migrations after deploy:
```bash
railway run python start.py --migrate-only
```

---

## Environment Variables (Railway)

Set these in Railway backend service → Variables:

- `EHOS_DATABASE_URL` = `postgresql://postgres:{PASSWORD}@postgres.railway.internal:5432/railway`
- `EHOS_ENVIRONMENT` = `production`
- `EHOS_DEBUG` = `false`
- `EHOS_JWT_SECRET_KEY` = (strong random key)

---

## Troubleshooting

**Build fails in GitHub Actions:**
- Check workflow logs: https://github.com/incodiy/ehos-backend-api/actions
- Common issues: Docker build error, GHCR auth issue

**Manual deployment fails (Railway CLI):**
- Verify token: `railway login` and re-authenticate
- Check project link: `railway link`
- Ensure backend service exists in Railway project

**Backend crashes after deploy:**
- Check Railway logs: Dashboard → Backend service → Logs
- Verify `EHOS_DATABASE_URL` is set
- Check if PostgreSQL is running and accessible
