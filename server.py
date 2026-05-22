# -*- coding: utf-8 -*-
import json
import re
import urllib.request
import urllib.error
from urllib.parse import unquote, urlparse, parse_qs
from datetime import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"
LOG_PATH = BASE_DIR / "purchase.log"
PUBLIC_DIR = BASE_DIR / "public"

SHOPS = {
    "daodao": {"name": "叨叨", "baseUrl": "https://www.hnlsxxkj.com", "skuId": 1074},
    "niuniu": {"name": "牛牛", "baseUrl": "https://www.whquxinyong.xyz", "skuId": 1071},
}


def normalize_config(cfg: dict) -> dict:
    if "daodao" in cfg and "niuniu" in cfg:
        return cfg
    preset = cfg.get(
        "presetQQ", ["869760112", "3324544625", "2325086810"]
    )
    return {
        "presetQQ": preset,
        "daodao": {
            "baseUrl": cfg.get("baseUrl", SHOPS["daodao"]["baseUrl"]),
            "skuId": cfg.get("skuId", SHOPS["daodao"]["skuId"]),
            "cookie": cfg.get("cookie", ""),
            "xsrfToken": cfg.get("xsrfToken", ""),
        },
        "niuniu": {
            "baseUrl": SHOPS["niuniu"]["baseUrl"],
            "skuId": SHOPS["niuniu"]["skuId"],
            "cookie": "",
            "xsrfToken": "",
        },
    }


def load_config():
    if not CONFIG_PATH.exists():
        example = BASE_DIR / "config.example.json"
        if example.exists():
            CONFIG_PATH.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            CONFIG_PATH.write_text(
                json.dumps(
                    {
                        "presetQQ": ["869760112", "3324544625", "2325086810"],
                        "daodao": {
                            "baseUrl": SHOPS["daodao"]["baseUrl"],
                            "skuId": SHOPS["daodao"]["skuId"],
                            "cookie": "",
                            "xsrfToken": "",
                        },
                        "niuniu": {
                            "baseUrl": SHOPS["niuniu"]["baseUrl"],
                            "skuId": SHOPS["niuniu"]["skuId"],
                            "cookie": "",
                            "xsrfToken": "",
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    normalized = normalize_config(cfg)
    if normalized != cfg:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(normalized, f, ensure_ascii=False, indent=2)
    return normalized


def get_shop_config(cfg: dict, shop: str) -> dict:
    if shop not in SHOPS:
        raise ValueError(f"未知店铺: {shop}")
    sc = cfg.get(shop, {})
    return {
        "shop": shop,
        "name": SHOPS[shop]["name"],
        "baseUrl": sc.get("baseUrl", SHOPS[shop]["baseUrl"]),
        "skuId": sc.get("skuId", SHOPS[shop]["skuId"]),
        "cookie": sc.get("cookie", ""),
        "xsrfToken": sc.get("xsrfToken", ""),
    }


def save_shop_config(shop: str, updates: dict):
    cfg = load_config()
    if shop not in SHOPS:
        raise ValueError(f"未知店铺: {shop}")
    block = cfg.setdefault(shop, {})
    for key in ("cookie", "xsrfToken", "skuId", "baseUrl"):
        if key in updates and updates[key] is not None:
            block[key] = updates[key]
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return get_shop_config(cfg, shop)


def mask_secret(text: str, show=8):
    if not text or len(text) <= show * 2:
        return "（未设置）" if not text else "***"
    return f"{text[:show]}...{text[-show:]}"


def build_payment_url(config, order_number: str) -> str:
    return f"{config['baseUrl']}/orders/{order_number}/NiupayPay?type=create"


def parse_xsrf_from_cookie(cookie: str):
    m = re.search(r"XSRF-TOKEN=([^;]+)", cookie)
    if not m:
        return None
    return unquote(m.group(1).strip())


def parse_cookie_string(cookie_str: str) -> dict:
    cookies = {}
    for part in (cookie_str or "").split(";"):
        part = part.strip()
        if "=" in part:
            key, val = part.split("=", 1)
            cookies[key.strip()] = val.strip()
    return cookies


def cookies_to_string(cookies: dict) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


def merge_set_cookie_headers(cookies: dict, headers) -> dict:
    from http.cookies import SimpleCookie

    if hasattr(headers, "get_all"):
        set_cookies = headers.get_all("Set-Cookie") or []
    else:
        one = headers.get("Set-Cookie")
        set_cookies = [one] if one else []
    for header in set_cookies:
        if not header:
            continue
        sc = SimpleCookie()
        sc.load(header)
        for key, morsel in sc.items():
            cookies[key] = morsel.value
    return cookies


class ShopSession:
    """同一订单流程内自动合并 Set-Cookie，避免 CSRF token mismatch。"""

    def __init__(self, config: dict):
        self.base_url = config["baseUrl"]
        self.cookies = parse_cookie_string(config.get("cookie", ""))
        xsrf = config.get("xsrfToken") or ""
        if xsrf and "XSRF-TOKEN" not in self.cookies:
            self.cookies["XSRF-TOKEN"] = xsrf
        self._sync_xsrf_header()

    def _sync_xsrf_header(self):
        cookie_str = cookies_to_string(self.cookies)
        self.xsrf = parse_xsrf_from_cookie(cookie_str) or ""

    def cookie_string(self) -> str:
        return cookies_to_string(self.cookies)

    def build_headers(self, with_json=False) -> dict:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Connection": "keep-alive",
            "Cookie": self.cookie_string(),
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
            ),
            "Origin": self.base_url,
            "Referer": f"{self.base_url}/products/197",
        }
        if self.xsrf:
            headers["X-XSRF-TOKEN"] = self.xsrf
        if with_json:
            headers["Content-Type"] = "application/json"
        return headers

    def request(self, path: str, method="GET", body=None):
        url = f"{self.base_url}{path}"
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers=self.build_headers(body is not None), method=method
        )
        try:
            resp = urllib.request.urlopen(req, timeout=30)
            merge_set_cookie_headers(self.cookies, resp.headers)
            self._sync_xsrf_header()
            text = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(text)
            except json.JSONDecodeError:
                return resp.status, {"raw": text}
        except urllib.error.HTTPError as e:
            merge_set_cookie_headers(self.cookies, e.headers)
            self._sync_xsrf_header()
            text = e.read().decode("utf-8", errors="replace")
            try:
                return e.code, json.loads(text)
            except json.JSONDecodeError:
                return e.code, {"raw": text, "error": str(e)}


def write_log(message: str):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}\n"
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line)
    print(message)


def http_request_with_config(config, path, method="GET", body=None):
    session = ShopSession(config)
    return session.request(path, method, body)


def create_single_order(config, qq: str, logs: list, index: int, total: int):
    prefix = f"[{config['name']}]"

    def add_log(msg):
        full = f"{prefix} {msg}"
        write_log(full)
        logs.append({"time": datetime.now().isoformat(), "message": full})

    add_log(f"正在创建第 {index}/{total} 个订单…")

    session = ShopSession(config)
    status, _ = session.request("/carts/mini", "GET")
    add_log(f"第 {index} 个：GET /carts/mini 完成，HTTP {status}（已刷新 CSRF）")

    status, cart_data = session.request(
        "/carts",
        "POST",
        {"sku_id": config["skuId"], "quantity": 1, "buy_now": True},
    )

    if cart_data.get("status") != "success":
        err = cart_data.get("message", "")
        if "CSRF" in str(cart_data) or "CSRF" in err:
            msg = (
                f"第 {index} 个订单加购失败: CSRF 校验失败。"
                "请到「{0}cookie配置」用浏览器刚登录后的 Cookie 重新保存（GET 与 POST 须同一套）"
            ).format(config["name"])
        else:
            msg = f"第 {index} 个订单加购失败: {json.dumps(cart_data, ensure_ascii=False)}"
        add_log(msg)
        return None, msg

    add_log(f"第 {index} 个：加入购物车成功")

    status, confirm_data = session.request(
        "/checkout/confirm",
        "POST",
        {"comment": "", "qq": str(qq)},
    )

    order_number = confirm_data.get("number")
    if not order_number:
        msg = f"第 {index} 个订单获取失败: {json.dumps(confirm_data, ensure_ascii=False)}"
        add_log(msg)
        return None, msg

    payment_url = build_payment_url(config, order_number)
    add_log(f"第 {index} 个订单号: {order_number}")
    try:
        save_shop_config(
            config["shop"],
            {"cookie": session.cookie_string(), "xsrfToken": session.xsrf},
        )
    except Exception:
        pass
    return {
        "index": index,
        "orderNumber": order_number,
        "paymentUrl": payment_url,
    }, None


def run_purchase(shop: str, qq: str, quantity: int = 1):
    cfg = load_config()
    config = get_shop_config(cfg, shop)
    logs = []
    quantity = max(1, min(int(quantity), 20))
    orders = []
    prefix = f"[{config['name']}]"

    def add_log(msg):
        full = f"{prefix} {msg}"
        write_log(full)
        logs.append({"time": datetime.now().isoformat(), "message": full})

    add_log(f"开始购买，QQ: {qq}，将生成 {quantity} 个独立订单")

    for i in range(1, quantity + 1):
        order, err = create_single_order(config, qq, logs, i, quantity)
        if err:
            if orders:
                return {
                    "ok": False,
                    "shop": shop,
                    "logs": logs,
                    "orders": orders,
                    "message": f"已完成 {len(orders)}/{quantity} 个，后续失败: {err}",
                }
            return {"ok": False, "shop": shop, "logs": logs, "message": err, "orders": []}
        orders.append(order)

    add_log(f"全部完成，共 {len(orders)} 个订单")
    return {
        "ok": True,
        "shop": shop,
        "logs": logs,
        "orders": orders,
        "orderNumber": orders[-1]["orderNumber"] if orders else None,
        "paymentUrl": orders[-1]["paymentUrl"] if orders else None,
    }


def parse_shop_from_path(path: str, body: dict) -> str:
    shop = (body.get("shop") or "").strip()
    if shop in SHOPS:
        return shop
    parsed = urlparse(path)
    qs = parse_qs(parsed.query)
    if qs.get("shop") and qs["shop"][0] in SHOPS:
        return qs["shop"][0]
    return "daodao"


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PUBLIC_DIR), **kwargs)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/config":
            cfg = load_config()
            self._json_response({
                "presetQQ": cfg.get("presetQQ", []),
                "shops": {
                    k: {"name": SHOPS[k]["name"], "skuId": cfg[k].get("skuId", SHOPS[k]["skuId"])}
                    for k in SHOPS
                },
            })
            return
        if path == "/api/settings":
            try:
                shop = parse_shop_from_path(self.path, {})
                qs = parse_qs(urlparse(self.path).query)
                if qs.get("shop"):
                    shop = qs["shop"][0]
                if shop not in SHOPS:
                    self._json_response({"ok": False, "message": f"未知店铺: {shop}"}, 400)
                    return
                cfg = load_config()
                sc = get_shop_config(cfg, shop)
                self._json_response({
                    "shop": shop,
                    "name": sc["name"],
                    "baseUrl": sc["baseUrl"],
                    "skuId": sc["skuId"],
                    "cookie": sc["cookie"],
                    "xsrfToken": sc["xsrfToken"],
                    "cookiePreview": mask_secret(sc["cookie"]),
                    "xsrfPreview": mask_secret(sc["xsrfToken"]),
                })
            except Exception as e:
                self._json_response({"ok": False, "message": str(e)}, 500)
            return
        if path == "/api/logs":
            text = LOG_PATH.read_text(encoding="utf-8") if LOG_PATH.exists() else ""
            self._json_response({"logs": text})
            return
        super().do_GET()

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length).decode("utf-8") or "{}")

    def do_POST(self):
        body = self._read_json_body()
        shop = parse_shop_from_path(self.path, body)

        if self.path.split("?")[0] == "/api/settings":
            cookie = (body.get("cookie") or "").strip()
            xsrf = (body.get("xsrfToken") or "").strip()
            if not cookie:
                self._json_response({"ok": False, "message": "Cookie 不能为空"}, 400)
                return
            if not xsrf:
                xsrf = parse_xsrf_from_cookie(cookie) or ""
            if not xsrf:
                self._json_response({
                    "ok": False,
                    "message": "未能从 Cookie 解析 XSRF-TOKEN，请手动填写",
                }, 400)
                return
            try:
                sc = save_shop_config(shop, {"cookie": cookie, "xsrfToken": xsrf})
                write_log(f"[{sc['name']}] Cookie 配置已更新")
                self._json_response({
                    "ok": True,
                    "message": f"{sc['name']} 配置已保存",
                    "cookiePreview": mask_secret(sc["cookie"]),
                    "xsrfPreview": mask_secret(sc["xsrfToken"]),
                })
            except Exception as e:
                self._json_response({"ok": False, "message": str(e)}, 500)
            return

        if self.path.split("?")[0] == "/api/settings/test":
            cookie = (body.get("cookie") or "").strip()
            xsrf = (body.get("xsrfToken") or "").strip()
            cfg = load_config()
            sc = get_shop_config(cfg, shop)
            test_cfg = {
                **sc,
                "cookie": cookie or sc["cookie"],
                "xsrfToken": xsrf or sc["xsrfToken"] or parse_xsrf_from_cookie(cookie or ""),
            }
            if not test_cfg["cookie"]:
                self._json_response({"ok": False, "message": "请先填写 Cookie"}, 400)
                return
            if not test_cfg["xsrfToken"]:
                test_cfg["xsrfToken"] = parse_xsrf_from_cookie(test_cfg["cookie"]) or ""
            if not test_cfg["xsrfToken"]:
                self._json_response({"ok": False, "message": "缺少 XSRF Token"}, 400)
                return
            try:
                status, data = http_request_with_config(test_cfg, "/carts/mini", "GET")
                ok = status == 200
                csrf_ok = "CSRF" not in str(data)
                self._json_response({
                    "ok": ok and csrf_ok,
                    "message": (
                        f"{test_cfg['name']} Cookie 有效"
                        if ok and csrf_ok
                        else f"HTTP {status} 或 CSRF 无效，请重新复制 Cookie"
                    ),
                    "status": status,
                    "data": data,
                })
            except Exception as e:
                self._json_response({"ok": False, "message": f"测试失败: {e}"}, 500)
            return

        if self.path.split("?")[0] != "/api/purchase":
            self.send_error(404)
            return

        qq = (body.get("qq") or "").strip()
        quantity = int(body.get("quantity") or 1)
        if not qq:
            self._json_response({"ok": False, "message": "请填写 QQ 号"}, 400)
            return
        try:
            result = run_purchase(shop, qq, quantity)
            self._json_response(result)
        except Exception as e:
            write_log(f"请求异常: {e}")
            self._json_response({"ok": False, "message": str(e)}, 500)

    def _json_response(self, data, code=200):
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    import os

    port = int(os.environ.get("PORT", "8765"))
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"充值网站已启动，端口 {port}")
    server.serve_forever()
