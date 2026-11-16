#!/usr/bin/env python3
"""Generate a private, self-contained phone setup page. No network access.

Reads webhook keys only when invoked explicitly. Never prints their values.
"""
from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path
import stat
import tempfile
from urllib.parse import urlsplit, urlunsplit


def webhook_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment):
        raise ValueError('URL must be a plain HTTPS service URL without credentials, query or fragment')
    if parsed.path.rstrip('/') not in ('', '/index'):
        raise ValueError('URL path must be empty or /index')
    if any(character.isspace() or ord(character) < 32 for character in parsed.netloc):
        raise ValueError('Invalid service hostname')
    return urlunsplit(('https', parsed.netloc, '/index', '', ''))


def render_page(url: str, keys: dict) -> str:
    if not isinstance(keys, dict):
        raise ValueError('Keys file must contain a JSON object')
    bearer = keys.get('bearer')
    secret = keys.get('signing_secret')
    for value in (bearer, secret):
        if (not isinstance(value, str) or not 32 <= len(value) <= 1024
                or any(ord(character) < 32 or ord(character) == 127 for character in value)):
            raise ValueError('Keys file must contain valid bearer and signing_secret strings')
    escaped_url = html.escape(webhook_url(url), quote=True)
    escaped_auth = html.escape('Bearer ' + bearer, quote=True)
    escaped_secret = html.escape(secret, quote=True)
    return '''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'none'; img-src 'none'; form-action 'none'; base-uri 'none'">
<title>戒指连接 Muse · 手机配置</title>
<style>
body{font:17px/1.65 system-ui,-apple-system,sans-serif;color:#22322d;background:#f2f5f1;max-width:780px;margin:32px auto;padding:20px}
h1{font-size:28px}h2{font-size:21px;margin-top:0}section{background:white;border-radius:16px;padding:22px;margin:20px 0}
.private{padding:15px;border:1px solid #b97d39;border-radius:10px;background:#fff4dc}.field{margin:16px 0}label{display:block;font-weight:600}
input{box-sizing:border-box;width:100%;padding:11px;margin:6px 0;font:14px/1.5 ui-monospace,monospace;border:1px solid #bac9c0;border-radius:7px;color:#162b22;background:#fafcfa}
button{font:inherit;font-size:15px;border:1px solid #9ab2a4;border-radius:7px;padding:6px 14px;background:#eff7f0;color:#174c35;cursor:pointer;margin:3px 8px 3px 0}
small{color:#59685f}li{margin-bottom:8px}.status{min-height:28px;color:#245b3a}code{background:#eef3ed;padding:2px 5px;border-radius:4px}
</style></head><body>
<h1>💍 戒指 → Muse：手机配置</h1>
<p class="private"><strong>此文件包含私人密钥，请勿分享、上传或发截图。</strong><br>这是本机配置文件，没有外部资源，也不会发送网络请求。隐藏字段仅用于避免旁人看到；文件本身仍包含完整密钥。</p>
<section><h2>1 · 打开 iPhone 的 Pebble App</h2>
<p>以下路径适用于 iPhone 上的 <strong>Pebble 1.18.0</strong>；其他版本的菜单位置可能不同：</p>
<ol>
<li>点底部 <strong>Index</strong> 标签。如果不在底栏，点 <strong>More → Index</strong>。</li>
<li>点 <strong>Index 页面右上角的齿轮</strong>，进入 Settings。</li>
<li>找到 <strong>Ring Button</strong>。选择要发送给 Muse 的手势，例如 <strong>Double click &amp; hold（双击后按住）→ Webhook only</strong>。其他手势可以继续使用 Index Agent。</li>
<li>在双击并按住手势下打开 <strong>Webhook settings</strong>。打开后的标题应为 <strong>Webhook · Double click &amp; hold</strong>；如果需要先展开菜单，点该行右侧显示当前动作的方框。</li>
<li>这条云端链路接收的是手机实际发送到此 Webhook 的录音或文字。测试时使用上一步选择的手势。</li>
</ol>
<p>上传配对凭证前，请停止使用同一设备身份的本地 SDK 连接，以免与云端冲突。完成一次配对并部署云端后，日常使用无需一直开着本地电脑，也不需要 VPN 或给云端主机配对蓝牙。</p></section>
<section><h2>2 · 按下面内容填写并保存</h2>
<div class="field"><label for="url">Webhook URL</label><input id="url" readonly value="''' + escaped_url + '''"><button type="button" data-copy="url">复制地址</button></div>
<div class="field"><label>添加 Header：名称为 <code>Authorization</code></label><small>值必须包含开头的 Bearer 和它后面的一个空格。</small>
<input id="auth" type="password" aria-label="Authorization Header 值" readonly value="''' + escaped_auth + '''"><button type="button" data-copy="auth">复制 Header 值</button><button type="button" data-toggle="auth" aria-pressed="false">显示</button></div>
<div class="field"><label for="secret">开启 Sign requests（请求签名），Signing secret 填：</label><input id="secret" type="password" readonly value="''' + escaped_secret + '''"><button type="button" data-copy="secret">复制签名密钥</button><button type="button" data-toggle="secret" aria-pressed="false">显示</button></div>
<p>Payload／What to send 选择 <strong>Both（转写和录音）</strong>，点击 <strong>Save</strong>。这会等待 Pebble 转写完成，再将转写与录音一起发送。</p>
<p>云端把 Pebble 转写和录音放入<strong>同一条 Muse 消息</strong>，只提交一次。Recording 只发录音，Transcription 只发文字，仍然兼容。Muse App 是否显示可播放的录音由 App 决定，只有文字显示不能证明录音未送达。无效或超过 60 秒、8 MiB 的录音会报错，不会截断后执行。</p>
<p class="status" id="copy-status" role="status" aria-live="polite"></p>
<small>页面上的复制按钮把选中的值复制到本机剪贴板；你也可以显示字段后手动复制。不要把密钥粘贴到聊天里。</small></section>
<section><h2>3 · 完成两次实际测试</h2><ol>
<li>在 Pebble App 点击 <strong>Send test event</strong>。如果 Save 后设置页已关闭，重新打开同一手势的 Webhook 设置再测试。应显示发送成功；此测试不会向 Muse 下达任务。</li>
<li>使用所选手势按住戒指，说：<strong>“请只回复戒指连接成功。”</strong>松开按钮后等待，在 Pebble 核对转写与录音，再打开目标 Muse 聊天确认消息和回复。</li>
</ol><p>日常使用<strong>无需暗号或 Muse 前缀</strong>。录音和仅文字载荷都进入同一个 Muse 聊天，不按内容分类。默认是 Muse 主聊天；如果管理员显式设置了固定会话，则进入该会话。只有配置了此 Webhook 的手势才走这条链路。手机需在戒指附近并能联网。</p>
<p>Recording 可以避开等待 Pebble 转写，但手机后台接收、排队与上传仍可能延迟；云端不能处理尚未发出的录音。首次云端请求也可能需要启动时间。若没有收到回复，请记录测试事件是否成功、Pebble 何时收到录音及戒指指令时间，供部署管理员检查；不要发送密钥或配置页截图。</p>
<p><strong>这两步需要你拿手机和戒指亲自完成。</strong>只有真实指令抵达 Muse，才能算全链路验收通过。</p></section>
<script>
const status=document.getElementById('copy-status');
for(const button of document.querySelectorAll('[data-copy]'))button.addEventListener('click',async()=>{
 const input=document.getElementById(button.dataset.copy);let copied=false;
 try{if(navigator.clipboard&&window.isSecureContext){await navigator.clipboard.writeText(input.value);copied=true;}}catch{}
 if(!copied){const temporary=document.createElement('textarea');temporary.value=input.value;temporary.setAttribute('readonly','');temporary.style.position='fixed';temporary.style.left='-9999px';document.body.append(temporary);temporary.select();try{copied=document.execCommand('copy');}catch{}temporary.remove();}
 status.textContent=copied?'已复制。请粘贴到 Pebble App 对应字段。':'浏览器未允许复制，请点击“显示”后手动复制。';
});
for(const button of document.querySelectorAll('[data-toggle]'))button.addEventListener('click',()=>{
 const input=document.getElementById(button.dataset.toggle);const show=input.type==='password';input.type=show?'text':'password';button.textContent=show?'隐藏':'显示';button.setAttribute('aria-pressed',String(show));
});
</script></body></html>
'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True, help='Deployed Cloud Run HTTPS URL')
    parser.add_argument('--keys', type=Path, required=True, help='Private webhook JSON file')
    parser.add_argument('--output', type=Path, required=True, help='Owner-only HTML output file')
    args = parser.parse_args()
    try:
        destination = args.output.expanduser().absolute()
        if destination.resolve() == args.keys.expanduser().resolve():
            raise ValueError('Output must not overwrite the keys file')
        if stat.S_IMODE(args.keys.stat().st_mode) & 0o077:
            raise ValueError('Keys file must not be readable by group or other users; use chmod 600')
        page = render_page(args.url, json.loads(args.keys.read_text(encoding='utf-8')))
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.phone-setup-', suffix='.tmp', dir=destination.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(page)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    except (OSError, ValueError) as error:
        # Error messages above contain no key values; JSON errors expose only
        # line/column locations, not the private input text.
        parser.exit(2, 'Could not generate private phone setup: ' + str(error) + '\n')
    print('Private phone setup generated with owner-only permissions. Do not share this file.')


if __name__ == '__main__':
    main()
