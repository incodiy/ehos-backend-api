# CI/CD Workflow Documentation

## Deploy Backend + PostgreSQL to Railway

Workflow: `.github/workflows/deploy.yml`

### What it does:
1. **Build Docker image** on push to `main`
2. **Push to GHCR** (GitHub Container Registry)
3. **Deploy to Railway** backend service
4. **Run migrations** automatically
5. **Health check** backend API

### Prerequisites (GitHub Secrets):

Set these in GitHub repo Settings → Secrets and variables → Actions:

1. **RAILWAY_TOKEN** — Railway API token
   - Go to Railway → Account Settings → API Tokens → Create
   - Copy token to GitHub Secrets as `RAILWAY_TOKEN`

2. **RAILWAY_PROJECT_ID** — Railway project ID
   - Find in Railway dashboard URL: `https://railway.app/project/{PROJECT_ID}`
   - Add to GitHub Secrets as `RAILWAY_PROJECT_ID`

3. **EHOS_DATABASE_URL** — PostgreSQL connection string
   - Format: `postgresql://postgres:{password}@{host}:{port}/{database}`
   - Set in Railway backend service → Variables → `EHOS_DATABASE_URL`
   - (Can also add to GitHub Secrets if needed for logging)

### Environment Variables (Railway):

Set these in Railway backend service → Variables:

- `EHOS_DATABASE_URL` = `postgresql://postgres:PASSWORD@postgres.railway.internal:5432/railway`
- `EHOS_ENVIRONMENT` = `production`
- `EHOS_DEBUG` = `false`
- `EHOS_JWT_SECRET_KEY` = (strong random key)

### Trigger Deployment:

Just push to `main`:
```bash
git add .
git commit -m "feat: something"
git push origin main
```

Workflow runs automatically. Check GitHub Actions tab for logs.

### Troubleshooting:

**Workflow fails at "Deploy to Railway" step:**
- Check `RAILWAY_TOKEN` and `RAILWAY_PROJECT_ID` in GitHub Secrets
- Ensure Railway backend service name is `backend`

**Migrations fail:**
- Check logs: GitHub Actions → deploy-railway job → "Run migrations on Railway" step
- Verify `EHOS_DATABASE_URL` is set in Railway backend Variables

**Health check fails:**
- Wait a few minutes for backend to fully start
- Check Railway backend logs: https://railway.app/project/{PROJECT_ID}
