module.exports = {
  apps: [
    {
      name: 'medlens-pipeline',
      script: 'npm',
      args: 'run pipeline',
      // Cron expression to run the pipeline every 6 hours
      // The pipeline runs a batch of scraping, interlinking, and enriching, then exits.
      // PM2 will restart it on this cron schedule.
      cron_restart: '0 */6 * * *',
      // Ensure it doesn't auto-restart immediately when it completes successfully
      // It will only restart when the cron schedule hits
      autorestart: false,
      watch: false,
      env: {
        NODE_ENV: 'production',
      },
      log_date_format: 'YYYY-MM-DD HH:mm Z',
      error_file: 'logs/pipeline-error.log',
      out_file: 'logs/pipeline-out.log',
      merge_logs: true
    }
  ]
};
