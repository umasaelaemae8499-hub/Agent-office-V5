# Running n8n locally on http://localhost:5678

n8n must run **on your own PC** to be reachable at your `localhost:5678`.
Pick whichever option you prefer.

---

## Option A — Docker (recommended)

Requires [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Windows/Mac)
or Docker Engine (Linux).

From this project folder:

```bash
docker compose up -d
```

Then open: **http://localhost:5678**

Useful commands:

```bash
docker compose logs -f n8n   # view logs
docker compose down          # stop n8n (data is kept in the n8n_data volume)
docker compose pull          # update to the latest n8n image, then `up -d` again
```

> **Before real use:** change `N8N_ENCRYPTION_KEY` in `docker-compose.yml` to a long
> random string. It encrypts your stored credentials, so keep it stable and private.

---

## Option B — npx (no Docker, needs Node.js 18+)

Install [Node.js](https://nodejs.org/) (LTS), then:

```bash
npx n8n
```

Open: **http://localhost:5678**

---

## Option C — global npm install

```bash
npm install -g n8n
n8n start
```

Open: **http://localhost:5678**

---

## First run

On first load, n8n asks you to create an owner account (email + password).
That account is stored locally on your machine.

## Changing the port

n8n defaults to `5678`. To use another port:

- **Docker:** edit the `ports:` mapping in `docker-compose.yml` (e.g. `"8080:5678"`).
- **npx / npm:** set the env var, e.g. `N8N_PORT=8080 npx n8n`.
