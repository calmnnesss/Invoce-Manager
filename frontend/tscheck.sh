#!/usr/bin/env bash
# 在前端容器里做类型检查。宿主的 npx 会解析到 Windows 侧的 node，
# 在 UNC 路径下跑不起来，所以走容器。
cd /home/lucas/code/Invoice-Manager
docker exec invoice_frontend sh -c 'cd /app && ./node_modules/.bin/tsc --noEmit'
echo "tsc exit=$?"
