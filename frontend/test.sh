#!/usr/bin/env bash
# 在前端容器里跑单元测试（vitest）。宿主的 npx 会解析到 Windows 侧的 node，
# 在 UNC 路径下跑不起来，所以和 tscheck.sh 一样走容器。
cd /home/lucas/code/Invoice-Manager
docker exec invoice_frontend sh -c 'cd /app && ./node_modules/.bin/vitest run'
echo "vitest exit=$?"
