// Story Kit capture. The camera and the voice recorder take the whole screen; what you
// capture lands on "Say something about it"; Send puts it in the inbox. Nothing you
// added is lost on a failed send, and a person is never shown a browser error.
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var form = $('send'), home = $('home'), say = $('say'), cam = $('cam');
  if (!form || !cam || !cam.showModal) return;

  var words = $('words'), goBtn = $('go'), when = $('happened_on'), consent = $('consent');
  var camVideo = $('cam-video'), camVoice = $('cam-empty'), camTimer = $('cam-timer'), camVtimer = $('cam-vtimer'), camWave = $('cam-wave');
  var camPreviewImg = $('cam-preview-img'), camPreviewVideo = $('cam-preview-video');
  var camSnap = $('cam-snap'), camRecord = $('cam-record'), camStop = $('cam-stop'), camSave = $('cam-save');
  var camRetake = $('cam-retake'), camPause = $('cam-pause'), camPick = $('cam-pick'), camHint = $('cam-hint');
  var camModeText = $('cam-mode-text'), camDot = $('cam-dot');
  var camFallback = $('cam-fallback'), camFallbackMsg = $('cam-fallback-msg'), camRetry = $('cam-retry');
  var VIDEO_LIMIT = 180; // seconds: the Video tile promises "up to 3 minutes"

  var state = { mode: null, stream: null, recorder: null, chunks: [], blob: null, mime: null,
                acc: 0, t0: 0, timerId: null, seconds: 0, raf: 0, ac: null, bars: [], lastBar: 0 };

  function fmt(s) { s = Math.max(0, Math.floor(s)); var m = Math.floor(s / 60), r = s % 60; return m + ':' + (r < 10 ? '0' : '') + r; }
  function toast(text) { if (window.storyKitToast) window.storyKitToast(text); }
  function icon(name, cls) { return '<svg class="ic ' + (cls || '') + '" aria-hidden="true" focusable="false"><use href="#i-' + name + '"></use></svg>'; }

  // ── the clock ──
  function elapsed() { return state.acc + (state.t0 ? Date.now() - state.t0 : 0); }
  function paint() { var t = fmt(elapsed() / 1000); camTimer.textContent = t; camVtimer.textContent = t; }
  function startTimer() {
    state.acc = 0; state.t0 = Date.now(); paint();
    state.timerId = setInterval(function () {
      paint();
      if (state.mode === 'video' && elapsed() / 1000 >= VIDEO_LIMIT) stopRecording();
    }, 250);
  }
  function stopTimer() {
    if (state.timerId) { clearInterval(state.timerId); state.timerId = null; }
    state.seconds = Math.round(elapsed() / 1000); state.acc = 0; state.t0 = 0;
  }

  // ── what the screen shows, from one place ──
  function ready() {
    if (!state.stream) return false;
    return state.mode === 'voice' ? true : camVideo.videoWidth > 0;
  }
  function recording() { return !!(state.recorder && state.recorder.state !== 'inactive'); }
  function paused() { return !!(state.recorder && state.recorder.state === 'paused'); }

  function setUI() {
    var has = !!state.blob, rec = recording(), can = ready(), isPhoto = state.mode === 'photo', isVoice = state.mode === 'voice';
    var failed = !camFallback.hidden;
    camSnap.hidden = !(isPhoto && !has && can);
    camRecord.hidden = !(!isPhoto && !rec && !has && can);
    camStop.hidden = !rec;
    camPause.hidden = !rec;
    camPause.setAttribute('aria-label', paused() ? 'Resume' : 'Pause');
    camPause.querySelector('use').setAttribute('href', paused() ? '#i-play' : '#i-pause');
    camRetake.hidden = !has;
    camSave.hidden = !has; camSave.disabled = !has;
    camPick.hidden = rec || has;
    camVtimer.hidden = !(state.mode === 'video' && rec);
    camDot.hidden = !(rec && !paused());
    camModeText.textContent = rec ? (paused() ? 'Paused' : 'Recording') : ({ photo: 'Photo', video: 'Video', voice: 'Voice note' })[state.mode] || 'Camera';
    var hint = '';
    if (!failed) {
      if (has) hint = isPhoto ? 'Use this photo, or retake it.' : 'Use this one, or retake it.';
      else if (rec) hint = paused() ? 'Paused. Tap play to carry on.' : "Tap the square when you're done.";
      else if (!can) hint = isVoice ? 'Starting the microphone…' : 'Starting the camera…';
      else hint = isPhoto ? 'Frame it, then tap the circle.' : 'Tap the red circle to start.';
    }
    camHint.textContent = hint;
  }

  // Say what happened and what to do next, in plain words. Never show a browser error to a person.
  function friendly(a, b) {
    var err = (a && typeof a === 'object') ? a : b, kind = typeof a === 'string' ? a : 'device';
    if (err && window.console) console.warn('Story Kit capture:', err);
    var name = (err && err.name) || '', dev = state.mode === 'voice' ? 'microphone' : 'camera';
    var what = ({ photo: 'photo', video: 'video', voice: 'voice note' })[state.mode] || 'file';
    if (kind === 'unsupported') return "This browser can't record here. Choose a " + what + ' you already have, or open Story Kit in Safari or Chrome.';
    if (kind === 'notready') return 'The ' + dev + ' is still starting. Give it a moment, then tap Try again.';
    if (kind === 'start' || kind === 'recording') return "Recording didn't start. Tap Try again, or choose a " + what + ' you already have.';
    if (/NotAllowed|Security|PermissionDenied/.test(name)) return 'Story Kit needs your ' + dev + '. Turn it on for this site in your browser settings, then tap Try again.';
    if (/NotFound|Overconstrained/.test(name)) return 'No ' + dev + ' was found on this device. You can choose a ' + what + ' you already have.';
    if (/NotReadable|Abort/.test(name)) return 'Another app is using the ' + dev + '. Close it, then tap Try again.';
    return 'The ' + dev + " didn't start. Tap Try again, or choose a " + what + ' you already have.';
  }
  function showFallback(msg, noRetry) { camFallbackMsg.textContent = msg; camRetry.hidden = !!noRetry; camFallback.hidden = false; setUI(); }
  function clearFallback() { camFallback.hidden = true; camFallbackMsg.textContent = ''; }

  // ── the live voice meter: bars fill left to right as you speak ──
  function drawBars(canvas, bars, filled, color, dim) {
    var dpr = window.devicePixelRatio || 1, w = canvas.clientWidth, h = canvas.clientHeight;
    if (canvas.width !== Math.round(w * dpr)) { canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr); }
    var g = canvas.getContext('2d'); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h);
    var bw = 4, gap = 3, n = Math.max(8, Math.floor((w + gap) / (bw + gap)));
    return { n: n, paint: function (i, amp, on) {
      var bh = Math.max(4, amp * h); g.fillStyle = on ? color : dim;
      var x = i * (bw + gap), y = (h - bh) / 2, r = bw / 2;
      g.beginPath(); g.moveTo(x + r, y); g.arcTo(x + bw, y, x + bw, y + bh, r); g.arcTo(x + bw, y + bh, x, y + bh, r);
      g.arcTo(x, y + bh, x, y, r); g.arcTo(x, y, x + bw, y, r); g.fill(); } };
  }
  function drawMeter() {
    var d = drawBars(camWave, state.bars, 0, '#6fd9b3', 'rgba(255,255,255,.22)');
    var shown = state.bars.slice(-d.n);
    for (var i = 0; i < d.n; i++) d.paint(i, i < shown.length ? shown[i] : 0, i < shown.length);
  }
  function startMeter(stream) {
    state.bars = []; drawMeter();
    var AC = window.AudioContext || window.webkitAudioContext;
    if (!AC || !state.ac) return;
    try {
      var src = state.ac.createMediaStreamSource(stream), an = state.ac.createAnalyser();
      an.fftSize = 512; src.connect(an);
      var buf = new Uint8Array(an.fftSize);
      (function frame(t) {
        state.raf = requestAnimationFrame(frame);
        if (!recording() || paused() || t - state.lastBar < 70) return;
        state.lastBar = t; an.getByteTimeDomainData(buf);
        var peak = 0; for (var i = 0; i < buf.length; i++) peak = Math.max(peak, Math.abs(buf[i] - 128));
        state.bars.push(Math.min(1, peak / 90)); if (state.bars.length > 400) state.bars.shift();
        drawMeter();
      })(0);
    } catch (e) { /* the recording does not need the meter */ }
  }
  function stopMeter() {
    if (state.raf) { cancelAnimationFrame(state.raf); state.raf = 0; }
    if (state.ac) { try { state.ac.close(); } catch (_) {} state.ac = null; }
  }

  // ── open, reset, close ──
  function resetCam() {
    if (state.stream) state.stream.getTracks().forEach(function (t) { t.stop(); });
    if (state.recorder && state.recorder.state !== 'inactive') { state.recorder.onstop = null; try { state.recorder.stop(); } catch (_) {} }
    stopMeter(); stopTimer();
    state.stream = null; state.recorder = null; state.chunks = []; state.blob = null; state.mime = null;
    camVideo.srcObject = null; camVideo.hidden = false;
    camPreviewImg.src = ''; camPreviewImg.hidden = true;
    camPreviewVideo.removeAttribute('src'); camPreviewVideo.load(); camPreviewVideo.hidden = true;
    camVoice.hidden = true; camVtimer.hidden = true; clearFallback();
    paint(); setUI();
  }
  function closeCam() { resetCam(); if (cam.open) cam.close(); }

  async function openMode(mode) {
    resetCam();
    state.mode = mode;
    var AC = window.AudioContext || window.webkitAudioContext;
    if (mode === 'voice' && AC) { try { state.ac = new AC(); state.ac.resume(); } catch (_) { state.ac = null; } } // inside the tap, so Safari lets it run
    if (!cam.open) cam.showModal();
    camVideo.hidden = mode === 'voice'; camVoice.hidden = mode !== 'voice';
    paint(); setUI();
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) { showFallback(friendly('unsupported'), true); return; }
    var wantsVideo = mode !== 'voice', wantsAudio = mode === 'voice' || mode === 'video';
    var tries = wantsVideo ? [{ video: { facingMode: 'environment' }, audio: wantsAudio }, { video: true, audio: wantsAudio }] : [{ audio: true }];
    var lastErr = null;
    for (var i = 0; i < tries.length; i++) {
      try {
        state.stream = await navigator.mediaDevices.getUserMedia(tries[i]);
        if (wantsVideo) {
          camVideo.srcObject = state.stream;
          try { await camVideo.play(); } catch (_) {}
          pollVideoReady();
        } else {
          startMeter(state.stream);
          setUI();
          startRecording();            // a voice note starts as soon as you let it listen
        }
        setUI();
        return;
      } catch (e) { lastErr = e; }
    }
    showFallback(friendly(lastErr || 'device'));
  }

  function pollVideoReady() {
    var tries = 0;
    function tick() { tries++; if (camVideo.videoWidth > 0 || tries >= 60) { setUI(); return; } requestAnimationFrame(tick); }
    if (camVideo.videoWidth > 0) { setUI(); return; }
    if (camVideo.requestVideoFrameCallback) camVideo.requestVideoFrameCallback(function () { setUI(); });
    requestAnimationFrame(tick);
  }
  function awaitReady(ms) {
    if (camVideo.videoWidth > 0) return Promise.resolve();
    return new Promise(function (resolve) {
      var done = false, finish = function () { if (!done) { done = true; resolve(); } };
      camVideo.addEventListener('loadeddata', finish, { once: true });
      camVideo.addEventListener('playing', finish, { once: true });
      setTimeout(finish, ms || 3000);
    });
  }

  async function snapPhoto() {
    await awaitReady(3000);
    var w = camVideo.videoWidth, h = camVideo.videoHeight;
    if (!w || !h) { showFallback(friendly('notready')); return; }
    var c = document.createElement('canvas'); c.width = w; c.height = h;
    c.getContext('2d').drawImage(camVideo, 0, 0, w, h);
    c.toBlob(function (b) {
      state.blob = b; state.mime = 'image/jpeg';
      camPreviewImg.src = URL.createObjectURL(b); camPreviewImg.hidden = false; camVideo.hidden = true;
      setUI();
    }, 'image/jpeg', 0.9);
  }

  // Webm first: Chrome and Firefox encode it reliably. iOS Safari has no webm at all, so it
  // falls through to mp4 there. Preferring mp4 on Chrome passes isTypeSupported() yet makes
  // files that will not play back.
  function pickMime(list, fallback) { for (var i = 0; i < list.length; i++) if (window.MediaRecorder && MediaRecorder.isTypeSupported(list[i])) return list[i]; return fallback; }
  function pickVideoMime() { return pickMime(['video/webm;codecs=vp9,opus', 'video/webm;codecs=vp8,opus', 'video/webm', 'video/mp4'], 'video/webm'); }
  function pickAudioMime() { return pickMime(['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4', 'audio/aac'], 'audio/webm'); }

  function startRecording() {
    if (!state.stream) { showFallback(friendly('notready')); return; }
    if (typeof MediaRecorder === 'undefined') { showFallback(friendly('unsupported'), true); return; }
    var mime = state.mode === 'video' ? pickVideoMime() : pickAudioMime(), rec;
    try { rec = new MediaRecorder(state.stream, { mimeType: mime }); state.mime = mime; }
    catch (e1) { try { rec = new MediaRecorder(state.stream); state.mime = rec.mimeType; } catch (e2) { showFallback(friendly('start', e2)); return; } }
    state.recorder = rec; state.chunks = [];
    rec.ondataavailable = function (e) { if (e.data && e.data.size) state.chunks.push(e.data); };
    rec.onerror = function (e) { showFallback(friendly('recording', e && e.error)); };
    rec.onstop = function () {
      state.blob = new Blob(state.chunks, { type: state.mime });
      if (state.mode === 'voice') { attach(); return; }            // straight on to "Say something about it"
      camPreviewVideo.src = URL.createObjectURL(state.blob); camPreviewVideo.hidden = false; camVideo.hidden = true;
      camPreviewVideo.play().catch(function () {});
      setUI();
    };
    try { rec.start(250); } catch (e) { showFallback(friendly('start', e)); return; }
    startTimer(); setUI();
  }
  function stopRecording() {
    stopTimer();
    if (state.recorder && state.recorder.state !== 'inactive') state.recorder.stop();
    setUI();
  }
  function togglePause() {
    if (!state.recorder) return;
    if (paused()) { state.recorder.resume(); state.t0 = Date.now(); }
    else if (state.recorder.state === 'recording') { state.recorder.pause(); state.acc = elapsed(); state.t0 = 0; }
    paint(); setUI();
  }
  function retake() {
    try { camPreviewVideo.pause(); } catch (_) {}
    camVideo.hidden = state.mode === 'voice';
    camPreviewImg.hidden = true;
    camPreviewVideo.removeAttribute('src'); camPreviewVideo.load(); camPreviewVideo.hidden = true;
    state.blob = null; state.mime = null; state.acc = 0; state.t0 = 0; paint();
    setUI();
  }

  function ext(m) { if (m.indexOf('mp4') !== -1) return 'mp4'; if (m.indexOf('jpeg') !== -1 || m.indexOf('jpg') !== -1) return 'jpg'; if (m.indexOf('png') !== -1) return 'png'; return 'webm'; }
  function attach() {
    if (!state.blob || !state.mode) return;
    var name = state.mode + '-' + new Date().toISOString().replace(/[:.]/g, '-') + '.' + ext(state.mime);
    var file = new File([state.blob], name, { type: state.mime });
    var dt = new DataTransfer(); dt.items.add(file);
    $('cap-' + state.mode).files = dt.files;
    form._seconds = state.mode === 'voice' ? state.seconds : 0;
    closeCam();
    refreshChosen();
  }

  function pickExisting() { if (state.mode) $('cap-pick-' + state.mode).click(); }
  ['photo', 'video', 'voice'].forEach(function (mode) {
    $('cap-pick-' + mode).addEventListener('change', function (e) {
      var f = e.target.files && e.target.files[0]; if (!f) return;
      var dt = new DataTransfer(); dt.items.add(new File([f], mode + '-' + f.name, { type: f.type }));
      $('cap-' + mode).files = dt.files; e.target.value = '';
      form._seconds = 0;
      closeCam(); refreshChosen();
    });
  });

  camSnap.addEventListener('click', snapPhoto);
  camRecord.addEventListener('click', startRecording);
  camStop.addEventListener('click', stopRecording);
  camPause.addEventListener('click', togglePause);
  camRetake.addEventListener('click', retake);
  camSave.addEventListener('click', attach);
  camPick.addEventListener('click', pickExisting);
  camRetry.addEventListener('click', function () { if (state.mode) openMode(state.mode); });
  $('cam-fallback-pick').addEventListener('click', pickExisting);
  $('cam-cancel').addEventListener('click', closeCam);
  cam.addEventListener('close', resetCam);                         // Esc closes it too
  camVideo.addEventListener('loadedmetadata', function () { if (state.stream) setUI(); });
  camVideo.addEventListener('playing', setUI);
  $('rec-photo').addEventListener('click', function () { openMode('photo'); });
  $('rec-video').addEventListener('click', function () { openMode('video'); });
  $('rec-voice').addEventListener('click', function () { openMode('voice'); });
  $('open-file').addEventListener('click', function () { $('pick').click(); });

  // ── what you chose, on "Say something about it" ──
  var audio = $('say-audio'), player = $('say-player'), playBtn = $('say-play'), wave = $('say-wave'), dur = $('say-dur');
  var thumbs = $('captures-thumbs'), urls = [], peaks = null;
  function allFiles() { var out = []; form.querySelectorAll('input[type=file]').forEach(function (i) { for (var f of i.files) out.push(f); }); return out; }
  function revoke() { urls.forEach(function (u) { try { URL.revokeObjectURL(u); } catch (_) {} }); urls = []; }
  function url(f) { var u = URL.createObjectURL(f); urls.push(u); return u; }

  function drawWave(progress) {
    var d = drawBars(wave, null, 0, '#0e6b57', '#9fb3a8'), n = d.n;
    for (var i = 0; i < n; i++) {
      var amp = peaks ? peaks[Math.min(peaks.length - 1, Math.floor(i * peaks.length / n))] : 0.08;
      d.paint(i, Math.max(.08, amp), i / n < progress);
    }
  }
  function loadPeaks(file) {
    var AC = window.AudioContext || window.webkitAudioContext;
    if (!AC || !file.arrayBuffer) return Promise.resolve(null);
    return file.arrayBuffer().then(function (buf) {
      var ac = new AC();
      return new Promise(function (res, rej) { ac.decodeAudioData(buf, res, rej); }).then(function (ab) {
        try { ac.close(); } catch (_) {}
        var ch = ab.getChannelData(0), n = 64, size = Math.max(1, Math.floor(ch.length / n)), out = [], top = 0.0001;
        for (var i = 0; i < n; i++) { var m = 0; for (var j = 0; j < size; j++) m = Math.max(m, Math.abs(ch[i * size + j] || 0)); out.push(m); top = Math.max(top, m); }
        return { peaks: out.map(function (v) { return v / top; }), seconds: ab.duration };
      });
    }).catch(function () { return null; });
  }
  function setPlaying(on) { playBtn.setAttribute('aria-label', on ? 'Pause' : 'Play'); playBtn.querySelector('use').setAttribute('href', on ? '#i-pause' : '#i-play'); }
  playBtn.addEventListener('click', function () { if (audio.paused) audio.play().catch(function () {}); else audio.pause(); });
  audio.addEventListener('play', function () { setPlaying(true); });
  audio.addEventListener('pause', function () { setPlaying(false); });
  audio.addEventListener('ended', function () { setPlaying(false); drawWave(0); });
  audio.addEventListener('timeupdate', function () { var d = isFinite(audio.duration) && audio.duration > 0 ? audio.duration : (form._seconds || 1); drawWave(audio.currentTime / d); });

  function renderPreviews(files) {
    revoke(); thumbs.innerHTML = ''; audio.pause(); audio.removeAttribute('src'); peaks = null;
    var soloAudio = files.length === 1 && files[0].type.indexOf('audio') === 0;
    player.hidden = !soloAudio; thumbs.hidden = soloAudio;
    if (soloAudio) {
      audio.src = url(files[0]); setPlaying(false);
      dur.textContent = fmt(form._seconds || 0); drawWave(0);
      loadPeaks(files[0]).then(function (r) { if (!r || audio.getAttribute('src') === null) return; peaks = r.peaks; dur.textContent = fmt(r.seconds || form._seconds || 0); drawWave(0); });
      return;
    }
    files.forEach(function (f) {
      var li = document.createElement('li');
      if (f.type.indexOf('image') === 0) { var img = document.createElement('img'); img.src = url(f); img.alt = f.name; li.appendChild(img); }
      else if (f.type.indexOf('video') === 0) { var v = document.createElement('video'); v.src = url(f); v.muted = true; v.playsInline = true; v.controls = true; li.appendChild(v); }
      else { var ic = document.createElement('span'); ic.className = 'file-ic'; ic.innerHTML = icon(f.type.indexOf('audio') === 0 ? 'mic' : 'file', 'ic-xl'); li.appendChild(ic); }
      var nm = document.createElement('span'); nm.className = 'nm'; nm.textContent = f.name; li.appendChild(nm);
      thumbs.appendChild(li);
    });
  }

  function showSay() {
    if (!say.hidden) return;
    home.hidden = true; say.hidden = false; document.body.classList.add('saying');
    window.scrollTo(0, 0);
    var t = $('say-title'); t.tabIndex = -1; t.focus({ preventScroll: true });
  }
  function showHome() {
    say.hidden = true; home.hidden = false; document.body.classList.remove('saying');
    window.scrollTo(0, 0);
  }
  function refreshChosen() {
    var files = allFiles();
    renderPreviews(files);
    if (files.length) showSay(); else if (!say.hidden) showHome();
    updateSend();
  }
  function updateSend() { goBtn.disabled = (allFiles().length === 0 && !words.value.trim()) || !!form._sending; }
  words.addEventListener('input', updateSend);
  form.addEventListener('change', function (e) { if (e.target.type === 'file') refreshChosen(); });

  function clearAll() {
    form.querySelectorAll('input[type=file]').forEach(function (i) { i.value = ''; });
    words.value = ''; when.value = ''; consent.checked = false; setTags([]); form._seconds = 0;
    $('send-error').hidden = true; $('sending').hidden = true;
    refreshChosen(); showHome();
  }
  $('say-back').addEventListener('click', function () {
    if (allFiles().length || words.value.trim()) $('discard').showModal(); else showHome();
  });
  $('discard-go').addEventListener('click', clearAll);

  // ── tags: type, press return, they become chips ──
  var tags = [], tagBox = $('tags'), tagInput = $('tag-input'), tagsValue = $('tags-value');
  function setTags(list) {
    tags = list.slice();
    tagBox.querySelectorAll('.tag').forEach(function (n) { n.remove(); });
    tags.forEach(function (t) {
      var chip = document.createElement('span'); chip.className = 'tag'; chip.appendChild(document.createTextNode(t));
      var x = document.createElement('button'); x.type = 'button'; x.setAttribute('aria-label', 'Remove tag ' + t); x.innerHTML = icon('x', 'ic-sm');
      x.addEventListener('click', function () { setTags(tags.filter(function (y) { return y !== t; })); tagInput.focus(); });
      chip.appendChild(x); tagBox.insertBefore(chip, tagInput);
    });
    tagsValue.value = tags.join(', ');
  }
  function takeTag() {
    var t = tagInput.value.replace(/[,;]/g, ' ').trim().toLowerCase().slice(0, 60); tagInput.value = '';
    if (t && tags.indexOf(t) === -1) setTags(tags.concat([t]));
  }
  tagInput.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); takeTag(); }
    else if (e.key === 'Backspace' && !tagInput.value && tags.length) setTags(tags.slice(0, -1));
  });
  tagInput.addEventListener('blur', takeTag);

  // ── Desktop: drop files anywhere on the drop area ──
  var drop = $('drop'), pick = $('pick');
  if (drop) {
    ['dragenter', 'dragover'].forEach(function (e) { drop.addEventListener(e, function (ev) { ev.preventDefault(); drop.classList.add('over'); }); });
    ['dragleave', 'drop'].forEach(function (e) { drop.addEventListener(e, function () { drop.classList.remove('over'); }); });
    drop.addEventListener('drop', function (ev) { ev.preventDefault(); pick.files = ev.dataTransfer.files; form._seconds = 0; refreshChosen(); });
  }

  // ── send ──
  var sending = $('sending'), bar = $('send-progress'), barText = $('send-text'), errBox = $('send-error'), xhr = null;
  function showError(msg) { errBox.textContent = msg; errBox.hidden = false; }
  window.addEventListener('beforeunload', function (e) { if (allFiles().length && !form._sending) { e.preventDefault(); e.returnValue = ''; } });

  function addRows(items) {
    var list = $('sent'), empty = $('sent-empty'); if (empty) empty.remove();
    document.querySelector('.sent-head h2').textContent = 'You sent this week';
    items.slice().reverse().forEach(function (it) {
      var li = document.createElement('li'), a = document.createElement('a'); a.href = it.url;
      var k = document.createElement('span'); k.className = 'kind-tile'; k.textContent = it.kind === 'text' ? 'note' : it.kind === 'data' ? 'number' : it.kind;
      var what = document.createElement('span'); what.className = 'what';
      var b = document.createElement('b'); b.textContent = it.title; var s = document.createElement('small'); s.textContent = it.when;
      what.appendChild(b); what.appendChild(s);
      var chip = document.createElement('span'); chip.className = 'state-chip inbox'; chip.textContent = 'In inbox';
      a.appendChild(k); a.appendChild(what); a.appendChild(chip); li.appendChild(a); list.insertBefore(li, list.firstChild);
    });
    while (list.children.length > 3) list.removeChild(list.lastChild);
  }

  form.addEventListener('submit', function (ev) {
    if (!window.XMLHttpRequest || !window.FormData) return;           // the plain form post still works
    ev.preventDefault();
    if (goBtn.disabled) return;
    takeTag();
    errBox.hidden = true; form._sending = true; updateSend();
    sending.hidden = false; bar.value = 0; barText.textContent = 'Sending…';
    xhr = new XMLHttpRequest(); xhr.open('POST', form.getAttribute('action') || location.href);
    xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest'); xhr.setRequestHeader('Accept', 'application/json');
    xhr.upload.onprogress = function (e) {
      if (!e.lengthComputable) return;
      var pct = Math.round(e.loaded * 100 / e.total); bar.value = pct; barText.textContent = pct < 100 ? 'Sending… ' + pct + '%' : 'Saving…';
    };
    function end() { form._sending = false; sending.hidden = true; updateSend(); }
    xhr.onload = function () {
      var r = {}; try { r = JSON.parse(xhr.responseText); } catch (_) {}
      end();
      if (xhr.status === 200 && r.ok) { clearAll(); addRows(r.items || []); toast(r.message || 'Sent to the inbox.'); }
      else showError((r.error || 'That did not go through.') + ' Everything you added is still here. Tap Send to inbox to try again.');
    };
    xhr.onerror = xhr.ontimeout = function () {
      end();
      showError((navigator.onLine === false ? 'You are offline.' : 'The connection dropped.') + ' Everything you added is still here. Tap Send to inbox when you are back online.');
    };
    xhr.onabort = function () { end(); showError('Stopped. Nothing was sent, and everything you added is still here.'); };
    xhr.send(new FormData(form));
  });
  $('send-cancel').addEventListener('click', function () { if (xhr) xhr.abort(); });

  updateSend();
})();
