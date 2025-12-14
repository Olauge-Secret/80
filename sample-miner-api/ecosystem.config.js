module.exports = {
    apps: [{
      name: 'miner-8000',
      cwd: '/home/ubuntu/snap/80/8000/sample-miner-api',
      script: '/home/ubuntu/snap/80/.venv/bin/python',
      args: 'run.py --port 8000 --provider vllm --save-messages',
      interpreter: 'none',
      instances: 1,
      autorestart: true,
      watch: false,
      max_memory_restart: '2G',
      env: {
        PYTHONUNBUFFERED: '1',
        PATH: '/home/ubuntu/snap/80/.venv/bin:' + process.env.PATH
      },
      error_file: '/home/ubuntu/snap/80/8000/sample-miner-api/logs/pm2-error.log',
      out_file: '/home/ubuntu/snap/80/8000/sample-miner-api/logs/pm2-out.log',
      log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
      merge_logs: true,
      min_uptime: '10s',
      max_restarts: 10
    }]
  };
  
  