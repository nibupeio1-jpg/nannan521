const express = require('express');
const fs = require('fs');
const path = require('path');

const app = express();
const PORT = 3000;
const LOG_FILE = path.join(__dirname, 'purchase.log');

app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

function loadConfig() {
  return JSON.parse(fs.readFileSync(path.join(__dirname, 'config.json'), 'utf8'));
}

function writeLog(message) {
  const line = `[${new Date().toLocaleString('zh-CN')}] ${message}\n`;
  fs.appendFileSync(LOG_FILE, line, 'utf8');
  console.log(message);
}

function buildHeaders(config, withJson = false) {
  const headers = {
    Accept: 'application/json, text/plain, */*',
    'Accept-Language': 'zh-CN,zh;q=0.9',
    Connection: 'keep-alive',
    Cookie: config.cookie,
    'X-XSRF-TOKEN': config.xsrfToken,
    'User-Agent':
      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
    Origin: config.baseUrl,
    Referer: `${config.baseUrl}/products/197`,
  };
  if (withJson) {
    headers['Content-Type'] = 'application/json';
  }
  return headers;
}

async function parseJson(res) {
  const text = await res.text();
  try {
    return JSON.parse(text);
  } catch {
    return { raw: text, status: res.status };
  }
}

app.get('/api/config', (req, res) => {
  const cfg = loadConfig();
  res.json({ presetQQ: cfg.presetQQ, skuId: cfg.skuId });
});

app.post('/api/purchase', async (req, res) => {
  const { qq, quantity = 1 } = req.body;
  if (!qq) {
    return res.status(400).json({ ok: false, message: '请填写 QQ 号' });
  }

  const config = loadConfig();
  const logs = [];

  const addLog = (msg) => {
    writeLog(msg);
    logs.push({ time: new Date().toISOString(), message: msg });
  };

  try {
    addLog(`开始购买，QQ: ${qq}，数量: ${quantity}`);

    const miniRes = await fetch(`${config.baseUrl}/carts/mini`, {
      method: 'GET',
      headers: buildHeaders(config),
    });
    const miniData = await parseJson(miniRes);
    addLog(`GET /carts/mini 完成，HTTP ${miniRes.status}`);

    const cartRes = await fetch(`${config.baseUrl}/carts`, {
      method: 'POST',
      headers: buildHeaders(config, true),
      body: JSON.stringify({
        sku_id: config.skuId,
        quantity: Number(quantity),
        buy_now: true,
      }),
    });
    const cartData = await parseJson(cartRes);

    if (cartData.status === 'success') {
      addLog('加入购物车成功');
    } else {
      const errMsg = `加入购物车失败: ${JSON.stringify(cartData)}`;
      addLog(errMsg);
      return res.json({ ok: false, logs, cartData, message: errMsg });
    }

    const confirmRes = await fetch(`${config.baseUrl}/checkout/confirm`, {
      method: 'POST',
      headers: buildHeaders(config, true),
      body: JSON.stringify({ comment: '', qq: String(qq) }),
    });
    const confirmData = await parseJson(confirmRes);

    const orderNumber = confirmData.number;
    if (orderNumber) {
      addLog(`订单号: ${orderNumber}`);
      return res.json({
        ok: true,
        logs,
        orderNumber,
        order: confirmData,
      });
    }

    const failMsg = `获取订单失败: ${JSON.stringify(confirmData)}`;
    addLog(failMsg);
    return res.json({ ok: false, logs, confirmData, message: failMsg });
  } catch (err) {
    const errMsg = `请求异常: ${err.message}`;
    addLog(errMsg);
    return res.status(500).json({ ok: false, logs, message: errMsg });
  }
});

app.get('/api/logs', (req, res) => {
  if (!fs.existsSync(LOG_FILE)) {
    return res.json({ logs: '' });
  }
  res.json({ logs: fs.readFileSync(LOG_FILE, 'utf8') });
});

app.listen(PORT, () => {
  console.log(`Q币充值网站已启动: http://localhost:${PORT}`);
});
