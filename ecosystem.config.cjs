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
      },
      max_memory_restart: "400M",
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
      max_memory_restart: "300M",
    },
    {
      name: "neuromule-wb-worker",
      script: "main.py",
      interpreter: "./venv/bin/python",
      cwd: __dirname,
      env: {
        NEUROMULE_PLATFORM: "wb_worker",
      },
      max_memory_restart: "300M",
    },
  ],
};
