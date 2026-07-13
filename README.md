# Enterprise Customs Engine

## Railway deployment with Docker

### 1) Create a GitHub repository
1. Open GitHub and sign in.
2. Click **New repository**.
3. Name it something like `enterprise-customs-engine`.
4. Keep it **Public** or **Private**.
5. Do not add a README if you already have files locally.
6. Create the repository.

### 2) Push your code to GitHub
From the project folder run:

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git push -u origin main
```

If your repo already exists locally, skip `git init`.

### 3) Deploy on Railway
1. Go to Railway and create a new project.
2. Choose **Deploy from GitHub repo**.
3. Connect your GitHub account.
4. Select your repository.
5. Set the project root to `backend` if Railway asks for a root directory.
6. Railway will detect the `Dockerfile` and build the container.

### 4) What Railway will run
The container starts with:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

### 5) Test URLs
- `/` for the root message
- `/health` for the health check
- `/docs` for Swagger UI

## Notes
- Docker is recommended for this project because it makes Railway deployment more reliable.
- Your code can still be improved later; Docker only packages what you have now.