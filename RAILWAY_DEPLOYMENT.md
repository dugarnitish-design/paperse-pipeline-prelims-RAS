# PaperSe Daily CA Pipeline — Railway Deployment

## Overview

Fully automated daily CA (Current Affairs) content generation and distribution pipeline:

1. **STEP 1:** Fetch Indian Express PDF from Gmail
2. **STEP 2:** Process news from IE PDF, PIB, SUJAS cache, Wikipedia
3. **STEP 3:** Generate 5-8 multiple-choice questions
4. **STEP 4:** Generate EN and HI PDFs with formatted content
5. **STEP 5:** Post to Telegram channel t.me/papersecivils

## Setup for Railway Deployment

### Prerequisites

- GitHub repository with this codebase
- Railway account ([railway.app](https://railway.app))
- Gmail account with app passwords enabled
- Telegram bot token & channel ID
- Supabase project with `daily_ca_items`, `daily_mcqs`, and other tables
- Anthropic API key

### Step 1: Railway Project Setup

1. Connect your GitHub repository to Railway
2. Create a new project and select this repository
3. Railway will automatically detect the Python environment

### Step 2: Environment Variables

In Railway dashboard, go to **Variables** and add all variables from `.env.example`:

```bash
# Supabase
SUPABASE_URL=https://nunbpwaxqqgfxrosqfhw.supabase.co
SUPABASE_SERVICE_KEY=<your-service-key>

# Anthropic
ANTHROPIC_API_KEY=<your-api-key>

# Gmail (requires Gmail App Password)
GMAIL_USER=dugarnitish@gmail.com
GMAIL_APP_PASSWORD=<your-app-password>

# Telegram
TELEGRAM_BOT_TOKEN=<your-bot-token>
TELEGRAM_CHANNEL_ID=<your-channel-id>
```

**Note:** Gmail app passwords are different from your regular Gmail password. [Enable 2FA and generate an app password](https://support.google.com/accounts/answer/185833).

### Step 3: Schedule Cron Job

In Railway dashboard, create a **Cron Job** trigger:

- **Cron Expression:** `0 1 * * *` (6:30 AM IST = 01:00 UTC)
- **Start Command:** `./pipelines/run_daily.sh`

**Timezone note:** Railway cron runs in UTC. `0 1 * * *` means 1:00 AM UTC, which is 6:30 AM IST.

### Step 4: Verify Deployment

Check Railway logs:
- Go to **Deployments** → **Logs**
- Monitor the first automated run to ensure all steps complete

## Local Testing

Before deploying, test the pipeline locally:

```bash
# Test with a specific date (e.g., June 2, 2026)
# Ensure IE PDF exists for June 1, 2026 at inputs/ie-pdf/2026-06-01.pdf
./pipelines/run_daily.sh 2026-06-02

# Run with dry-run Telegram (shows caption without posting)
# Already enabled in run_daily.sh
```

## Monitoring & Debugging

### Check Logs in Railway

```bash
# View latest logs
railway logs

# Follow live logs
railway logs -f
```

### Common Issues

**Gmail connection fails:**
- Verify `GMAIL_APP_PASSWORD` is correct (not your regular Gmail password)
- Check Gmail account has IMAP enabled
- Ensure 2-factor authentication is enabled

**Telegram posting fails:**
- Verify `TELEGRAM_BOT_TOKEN` is correct
- Verify `TELEGRAM_CHANNEL_ID` has the `-100` prefix if it's a private channel
- Ensure bot is a member of the channel with permission to post

**PDF generation fails:**
- Check IE PDF file exists and is readable
- Verify WeasyPrint dependencies are installed (handled by nixpacks)
- Check Supabase connectivity

**Supabase connection fails:**
- Verify `SUPABASE_URL` and `SUPABASE_SERVICE_KEY` are correct
- Check service key has permission to read/write tables
- Test connection locally first

## Disabling Dry-Run for Production

Currently, `run_daily.sh` runs Telegram delivery in **dry-run mode** (shows caption without posting).

To enable actual Telegram posting:

1. Edit `pipelines/run_daily.sh`
2. Remove `--dry-run` from the STEP 5 line:
   ```bash
   # Before:
   python3 pipelines/telegram_delivery.py "$DATE" --dry-run

   # After:
   python3 pipelines/telegram_delivery.py "$DATE"
   ```
3. Push the change and redeploy

## Output Files

Pipeline outputs are stored in:

- **PDFs:** `outputs/daily-ca/EN/YYYY-MM-DD.pdf` and `HI/YYYY-MM-DD.pdf`
- **Database:** `daily_ca_items` table in Supabase (EN + HI content)
- **Database:** `daily_mcqs` table in Supabase (5-8 generated questions)
- **Telegram:** Posted to t.me/papersecivils automatically

## Scaling & Troubleshooting

### If pipeline runs too long:

- Reduce PDF pages: edit `pdf_generator.py` page limit
- Reduce MCQ generation: edit `mcq_generator.py` count
- Check Supabase query performance

### If memory issues:

- Railway free tier has 512 MB RAM
- Reduce ChromaDB query batch size if needed
- Consider upgrading to paid Railway plan

### Manual re-run:

To manually trigger the pipeline for a past date:

```bash
cd /path/to/paperse-pipeline
./pipelines/run_daily.sh 2026-06-02
```

Then check the outputs in `outputs/daily-ca/`.

## Production Notes

- **Backups:** Supabase automatically backs up your database
- **API Rate Limits:**
  - Gmail: ~500 requests/day
  - Anthropic: subject to your subscription plan
  - Telegram: 30 messages/second per bot
- **Costs:** Railway free tier should handle daily runs. Monitor usage.

## Support

For issues, check:
1. Railway logs for error messages
2. Supabase dashboard for database status
3. Gmail security log for authentication failures
4. Telegram bot logs (via @BotFather on Telegram)
