/*
 * Record a short video and hand back the stored URL.
 *
 * A faithful vanilla port of trust_claim's src/components/VideoRecorder (React):
 * enable camera -> live mirrored self-view -> record with a pulsing dot + timer
 * -> stop -> playback -> auto-upload with a progress bar -> re-record / remove.
 * Kept deliberately gentle: clear steps, no surprises, friendly errors, and you
 * can always just write words instead.
 *
 *   <div id="v" data-lt-video-url-target="#id_video_url" data-lt-max="60"></div>
 *   <input type="hidden" id="id_video_url" name="video_url">
 *   <script src=".../video-recorder.js"></script>
 *   <script>LTVideo.attach(document.getElementById('v'), {ltApi:'https://live.linkedtrust.us'});</script>
 *
 * Video is uploaded to {ltApi}/api/video/upload and stored by LinkedTrust; only
 * the returned URL is written into the target field.
 */
(function (global) {
  'use strict';

  function el(tag, style, attrs) {
    var e = document.createElement(tag);
    if (style) e.setAttribute('style', style);
    if (attrs) Object.keys(attrs).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    return e;
  }
  function fmt(s) {
    var m = Math.floor(s / 60), r = s % 60;
    return m + ':' + (r < 10 ? '0' : '') + r;
  }

  function attach(root, opts) {
    opts = opts || {};
    var ltApi = (opts.ltApi || root.getAttribute('data-lt-api') || '').replace(/\/$/, '');
    var maxDuration = parseInt(root.getAttribute('data-lt-max') || opts.maxDuration || 60, 10);
    var urlSel = root.getAttribute('data-lt-video-url-target');
    var urlField = opts.urlField || (urlSel && document.querySelector(urlSel));

    var stream = null, recorder = null, chunks = [], blob = null, objUrl = null;
    var timer = null, seconds = 0, uploadedBlob = null;

    // ---- build UI ------------------------------------------------------------
    root.innerHTML = '';
    var stage = el('div', 'position:relative;width:100%;aspect-ratio:16/9;background:#000;border-radius:8px;overflow:hidden');
    var placeholder = el('div', 'position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;color:#fff;opacity:.75;font:400 .95rem/1.4 sans-serif;text-align:center;padding:1rem');
    placeholder.innerHTML = '<div style="font-size:2.4rem;margin-bottom:.3rem">🎥</div>Record a short video (optional)';
    var preview = el('video', 'width:100%;height:100%;object-fit:cover;display:none;transform:scaleX(-1)', { playsinline: '', muted: '' });
    preview.muted = true;
    var playback = el('video', 'width:100%;height:100%;object-fit:contain;background:#000;display:none', { controls: '', playsinline: '' });
    var recDot = el('div', 'position:absolute;top:10px;left:10px;display:none;align-items:center;gap:.4rem;background:rgba(0,0,0,.6);color:#fff;padding:.25rem .6rem;border-radius:6px;font:600 .85rem sans-serif');
    recDot.innerHTML = '<span style="width:10px;height:10px;border-radius:50%;background:#e53935;display:inline-block;animation:lt-pulse 1s infinite"></span><span class="lt-time">0:00</span>';
    var upWrap = el('div', 'position:absolute;inset:0;display:none;flex-direction:column;align-items:center;justify-content:center;background:rgba(0,0,0,.7);color:#fff;font:600 .9rem sans-serif;gap:.5rem');
    upWrap.innerHTML = '<div>Uploading… <span class="lt-pct">0</span>%</div><div style="width:60%;height:6px;background:rgba(255,255,255,.25);border-radius:3px;overflow:hidden"><div class="lt-bar" style="height:100%;width:0;background:#fff"></div></div>';
    var doneBadge = el('div', 'position:absolute;top:10px;right:10px;display:none;align-items:center;gap:.3rem;background:rgba(46,125,50,.92);color:#fff;padding:.25rem .6rem;border-radius:6px;font:600 .85rem sans-serif');
    doneBadge.textContent = '✓ Saved';
    stage.append(placeholder, preview, playback, recDot, upWrap, doneBadge);

    var err = el('div', 'display:none;color:#b00;font:400 .9rem/1.45 sans-serif;margin:.5rem 0');
    var controls = el('div', 'display:flex;gap:.5rem;flex-wrap:wrap;margin-top:.6rem');
    var caption = el('div', 'font:400 .8rem sans-serif;color:#6b7684;margin-top:.4rem');
    caption.textContent = 'Up to ' + maxDuration + ' seconds. You can redo it as many times as you like.';

    function btn(label, kind) {
      var b = el('button', 'cursor:pointer', { type: 'button' });
      b.className = 'btn' + (kind === 'ghost' ? ' ghost' : '');
      b.textContent = label;
      return b;
    }
    var bEnable = btn('🎥 Record your words');
    var bStart = btn('● Start recording');
    var bStop = btn('■ Stop');
    var bRedo = btn('Redo', 'ghost');
    var bRemove = btn('Remove', 'ghost');
    controls.append(bEnable, bStart, bStop, bRedo, bRemove);

    if (!document.getElementById('lt-pulse-style')) {
      var st = el('style'); st.id = 'lt-pulse-style';
      st.textContent = '@keyframes lt-pulse{0%,100%{opacity:1}50%{opacity:.35}}';
      document.head.appendChild(st);
    }
    root.append(stage, err, controls, caption);

    // ---- state ---------------------------------------------------------------
    function setErr(msg) { err.textContent = msg || ''; err.style.display = msg ? 'block' : 'none'; }
    function only() {
      var vis = {}; for (var i = 0; i < arguments.length; i++) vis[arguments[i]] = true;
      [bEnable, bStart, bStop, bRedo, bRemove].forEach(function (b) { b.style.display = 'none'; });
      if (vis.enable) bEnable.style.display = '';
      if (vis.start) bStart.style.display = '';
      if (vis.stop) bStop.style.display = '';
      if (vis.redo) bRedo.style.display = '';
      if (vis.remove) bRemove.style.display = '';
    }
    function show(node) {
      [placeholder, preview, playback].forEach(function (n) { n.style.display = 'none'; });
      if (node) node.style.display = node === preview || node === playback ? 'block' : 'flex';
    }
    function stopTracks() { if (stream) { stream.getTracks().forEach(function (t) { t.stop(); }); stream = null; } }

    function toIdle() {
      stopTracks(); if (timer) { clearInterval(timer); timer = null; }
      if (objUrl) { URL.revokeObjectURL(objUrl); objUrl = null; }
      blob = null; uploadedBlob = null; seconds = 0;
      recDot.style.display = 'none'; upWrap.style.display = 'none'; doneBadge.style.display = 'none';
      if (urlField) urlField.value = '';
      show(placeholder); only('enable'); setErr('');
    }

    // ---- camera / recording --------------------------------------------------
    async function enable() {
      setErr(''); only(); show(placeholder);
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: 'user', width: { ideal: 1280 }, height: { ideal: 720 } }, audio: true
        });
        preview.srcObject = stream; preview.muted = true; await preview.play();
        show(preview); only('start');
      } catch (e) {
        setErr('No camera here. If you opened this inside another app (WhatsApp, Instagram), open it in your browser instead — or just write a few words.');
        show(placeholder); only('enable');
      }
    }

    function start() {
      if (!stream) return;
      chunks = []; seconds = 0;
      var pref = ['video/webm;codecs=vp9', 'video/webm', 'video/mp4'].filter(function (t) {
        return typeof MediaRecorder.isTypeSupported === 'function' && MediaRecorder.isTypeSupported(t);
      })[0];
      try { recorder = pref ? new MediaRecorder(stream, { mimeType: pref }) : new MediaRecorder(stream); }
      catch (e) { setErr('This browser will not record video. Try Safari or Chrome, or just write a few words.'); return; }
      recorder.ondataavailable = function (e) { if (e.data && e.data.size) chunks.push(e.data); };
      recorder.onstop = function () {
        blob = new Blob(chunks, { type: (recorder && recorder.mimeType) || 'video/webm' });
        objUrl = URL.createObjectURL(blob);
        preparePlayback(objUrl);
        show(playback); recDot.style.display = 'none'; only('redo');
        upload(); // auto-upload so nothing is lost
      };
      recorder.start(1000);
      show(preview); recDot.style.display = 'flex'; only('stop');
      recDot.querySelector('.lt-time').textContent = fmt(0) + ' / ' + fmt(maxDuration);
      timer = setInterval(function () {
        seconds += 1;
        recDot.querySelector('.lt-time').textContent = fmt(seconds) + ' / ' + fmt(maxDuration);
        if (seconds >= maxDuration) stop();
      }, 1000);
    }

    function stop() {
      if (timer) { clearInterval(timer); timer = null; }
      if (recorder && recorder.state !== 'inactive') recorder.stop();
      stopTracks();
    }

    // MediaRecorder webm has no duration header: force the browser to measure it
    // so playback can seek and play, then return to the start.
    function preparePlayback(src) {
      playback.src = src;
      playback.onloadedmetadata = function () {
        if (playback.duration !== Infinity) return;
        var reset = function () { try { playback.currentTime = 0; } catch (e) {} };
        playback.addEventListener('seeked', reset, { once: true });
        playback.addEventListener('durationchange', reset, { once: true });
        try { playback.currentTime = 1e101; } catch (e) { reset(); }
        setTimeout(reset, 800);
      };
    }

    function upload() {
      if (!blob || uploadedBlob === blob) return;
      uploadedBlob = blob;
      upWrap.style.display = 'flex'; doneBadge.style.display = 'none';
      var pct = upWrap.querySelector('.lt-pct'), bar = upWrap.querySelector('.lt-bar');
      var fd = new FormData(); fd.append('video', blob, 'video.webm');
      var xhr = new XMLHttpRequest();
      xhr.open('POST', ltApi + '/api/video/upload');
      xhr.timeout = 120000;
      xhr.upload.onprogress = function (e) {
        if (e.lengthComputable) { var p = Math.round(e.loaded / e.total * 100); pct.textContent = p; bar.style.width = p + '%'; }
      };
      xhr.onload = function () {
        upWrap.style.display = 'none';
        if (xhr.status >= 200 && xhr.status < 300) {
          var url = ''; try { var d = JSON.parse(xhr.responseText); url = d.videoUrl || d.url || ''; } catch (e) {}
          if (url) {
            if (urlField) urlField.value = url;
            preparePlayback(url); // prefer the hosted copy (seekable)
            doneBadge.style.display = 'flex'; only('redo', 'remove');
            root.dispatchEvent(new CustomEvent('lt-video-uploaded', { detail: { videoUrl: url } }));
            if (opts.onUploaded) opts.onUploaded(url);
          } else { fail(); }
        } else { fail(); }
      };
      xhr.onerror = xhr.ontimeout = fail;
      function fail() { upWrap.style.display = 'none'; setErr('That video did not upload. Tap Redo to try again, or just send your words without it.'); only('redo'); }
      xhr.send(fd);
    }

    bEnable.onclick = enable;
    bStart.onclick = start;
    bStop.onclick = stop;
    bRedo.onclick = function () { toIdle(); enable(); };
    bRemove.onclick = toIdle;

    // preload an already-attached url (e.g. after a failed form submit)
    var existing = urlField && urlField.value;
    if (existing) { preparePlayback(existing); show(playback); doneBadge.style.display = 'flex'; only('redo', 'remove'); }
    else { toIdle(); }
  }

  global.LTVideo = { attach: attach };
})(window);
