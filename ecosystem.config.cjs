/**
 * PM2: один процесс = одна роль NEUROMULE_PLATFORM.
 * Без фиксированного env при restart все три main.py становятся telegram → Conflict.
 */
module.exports = {
  apps: [
    {
      name: "neuromule-tg",
      script: "main.py",
      interpreter: "./venv/bin/python",
      cwd: __dirname,
      env: {
        NEUROMULE_PLATFORM: "telegram",
        // Меньше фоновых cover-тасков — меньше пик RAM на 1GB VDS.
        BLOGGER_COVER_WORKERS_COUNT: "2",
      },
      // Перезапуск до системного OOM (на ~1GB ноде без swap убивали tg при ~220–270MB).
      max_memory_restart: "280M",
      exp_backoff_restart_delay: 3000,
      max_restarts: 20,
      min_uptime: "10s",
    },
    {
      name: "neuromule-api",
      script: "main.py",
      interpreter: "./venv/bin/python",
      cwd: __dirname,
      env: {
        NEUROMULE_PLATFORM: "api",
        API_PORT: "8000",
      },
      max_memory_restart: "200M",
      exp_backoff_restart_delay: 3000,
    },
    {
      name: "neuromule-wb-worker",
      script: "main.py",
      interpreter: "./venv/bin/python",
      cwd: __dirname,
      env: {
        NEUROMULE_PLATFORM: "wb_worker",
      },
      max_memory_restart: "200M",
      exp_backoff_restart_delay: 3000,
    },
  ],
};
