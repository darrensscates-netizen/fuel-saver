# FuelSaver 🚗⛽

Find the cheapest petrol and diesel.

## Features
- Live prices from the UK company APIs
- Compares nearby stations
- Calculates true cost (fuel price + 25p/mile travel)
- One-tap Google Maps navigation
- Supports Petrol (E10), Diesel (B7), Super Unleaded (E5)

---

## Local Development

### 1. Install Python dependencies
```bash
pip install -r requirements.txt
```

### 2. Set up your API credentials
```bash
cp .env.example .env
```
Edit `.env` and add your Fuel Finder API credentials (Client ID and Client Secret).

### 3. Load environment variables and run
```bash
# Mac/Linux:
export $(cat .env | xargs) && python app.py

# Windows (Command Prompt):
set FUEL_CLIENT_ID=your_id_here
set FUEL_CLIENT_SECRET=your_secret_here
python app.py
```

Visit http://localhost:5000 in your browser.

---

## Deploying to Render

### 1. Push to GitHub
```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/fuel-saver.git
git push -u origin main
```

### 2. Create a new Web Service on Render
- Go to https://render.com and log in
- Click **New → Web Service**
- Connect your GitHub repository
- Render will auto-detect the `render.yaml` config

### 3. Add your environment variables on Render
- In your Render service dashboard, go to **Environment**
- Add two variables:
  - `FUEL_CLIENT_ID` = your Client ID
  - `FUEL_CLIENT_SECRET` = your Client Secret
- Click **Save Changes** — Render will redeploy automatically

### 4. Add your custom domain (optional)
- In Render, go to **Settings → Custom Domains**
- Add your `.shop` domain and follow the DNS instructions

---

## Important: Never commit your API keys
Your `.env` file is in `.gitignore` and will never be uploaded to GitHub.
Always set credentials as environment variables in Render's dashboard.
