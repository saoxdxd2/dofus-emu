// proxy_tunnel.js - one deep-check tunnel shared by proxy_check.js and proxy_pool.js.
//
// TCP connect -> (SOCKS5 handshake | HTTP CONNECT) -> TLS -> GET one path.
// A tunnel passes only when the TLS target answers HTTP 200.

'use strict';

const net = require('net');
const tls = require('tls');

function requestThrough(entry, reqPath, opts, cb) {
  const timeoutMs = opts.timeoutMs;
  const targetHost = opts.targetHost;
  const targetPort = opts.targetPort;
  const userAgent = opts.userAgent;
  const sock = net.connect({ host: entry.host, port: entry.port });
  let done = false;
  const fail = (reason) => { if (done) return; done = true; try { sock.destroy(); } catch (e) {} cb(reason); };
  const timer = setTimeout(() => fail('timeout'), timeoutMs);

  const tlsFetch = () => {
    const t = tls.connect({ socket: sock, servername: targetHost, rejectUnauthorized: false }, () => {
      t.write('GET ' + reqPath + ' HTTP/1.1\r\nHost: ' + targetHost +
        '\r\nUser-Agent: ' + userAgent + '\r\nAccept: */*\r\nConnection: close\r\n\r\n');
    });
    let buf = '';
    t.on('data', (d) => { buf += d.toString('latin1'); if (buf.length > 8192) t.destroy(); });
    t.on('error', (e) => { clearTimeout(timer); fail('tls:' + (e.code || e.message)); });
    t.on('close', () => {
      clearTimeout(timer);
      const m = buf.match(/^HTTP\/1\.[01] (\d+)/);
      if (!m) return fail('no-http');
      if (m[1] !== '200') return fail('http ' + m[1]);
      done = true;
      cb(null);
    });
  };

  if (entry.scheme.startsWith('socks')) {
    let stage = 0;
    const wantAuth = !!entry.userinfo;
    const sendConnect = () => {
      const hostBuf = Buffer.from(targetHost, 'ascii');
      sock.write(Buffer.concat([Buffer.from([0x05, 0x01, 0x00, 0x03, hostBuf.length]), hostBuf,
        Buffer.from([(targetPort >> 8) & 0xff, targetPort & 0xff])]));
    };
    sock.on('connect', () => sock.write(wantAuth ? Buffer.from([0x05, 0x02, 0x00, 0x02]) : Buffer.from([0x05, 0x01, 0x00])));
    sock.on('error', (e) => fail(e.code || e.message));
    sock.on('data', (d) => {
      try {
        if (stage === 0) {
          if (d.length < 2 || d[0] !== 0x05) return fail('socks bad-ver');
          if (d[1] === 0x00) { sendConnect(); stage = 3; }
          else if (d[1] === 0x02 && wantAuth) {
            const parts = entry.userinfo.split(':');
            const u = Buffer.from(parts[0] || '', 'utf8'), p = Buffer.from(parts[1] || '', 'utf8');
            sock.write(Buffer.concat([Buffer.from([0x01, u.length]), u, Buffer.from([p.length]), p]));
            stage = 2;
          } else return fail('socks method ' + d[1]);
          return;
        }
        if (stage === 2) {
          if (!(d.length >= 2 && d[1] === 0x00)) return fail('socks auth-fail');
          sendConnect();
          stage = 3;
          return;
        }
        if (stage === 3) {
          if (d.length >= 2 && d[1] === 0x00) { sock.removeAllListeners('data'); tlsFetch(); }
          else fail('socks connect ' + (d[1] || '?'));
        }
      } catch (e) { fail('socks err'); }
    });
    return;
  }

  let auth = '';
  if (entry.userinfo) auth = 'Proxy-Authorization: Basic ' + Buffer.from(entry.userinfo, 'utf8').toString('base64') + '\r\n';
  sock.on('connect', () => sock.write('CONNECT ' + targetHost + ':' + targetPort + ' HTTP/1.1\r\nHost: ' + targetHost + ':' + targetPort + '\r\n' + auth + '\r\n'));
  sock.on('error', (e) => fail(e.code || e.message));
  let buf = '';
  sock.on('data', (d) => {
    buf += d.toString('latin1');
    if (buf.indexOf('\r\n\r\n') === -1) { if (buf.length > 4096) fail('http big-reply'); return; }
    const m = buf.match(/^HTTP\/1\.[01] (\d+)/);
    if (m && m[1] === '200') { sock.removeAllListeners('data'); tlsFetch(); }
    else fail('http connect ' + (m ? m[1] : 'bad'));
  });
}

module.exports = { requestThrough };
