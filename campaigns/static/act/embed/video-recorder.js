/*
 * Record-or-upload a short video and hand back the stored URL.
 *
 * Shipped by workers.vc and used from GovKit too (same as embed/cohort-nav.js),
 * so the doorway and a member's profile behave identically: same recorder, same
 * time limit, same words when the camera is not available. One copy, because
 * two would drift.
 *
 * It attaches to markup each app already owns rather than rendering its own —
 * the two apps style very differently, and nothing here should decide how a
 * button looks. Mark up the parts and call attach():
 *
 *   <div id="v">
 *     <button type="button" data-lt-video-record>Record video</button>
 *     <button type="button" data-lt-video-upload>Upload a file</button>
 *     <input type="file" accept="video/*" data-lt-video-file hidden>
 *     <span data-lt-video-status></span>
 *     <video controls data-lt-video-preview></video>
 *     <input type="hidden" name="video_url" data-lt-video-url>
 *   </div>
 *   LTVideo.attach(document.getElementById('v'), { ltApi: '…', onUploaded: fn });
 *
 * The upload goes straight from the browser to LinkedTrust, which is what makes
 * the URL trustworthy: the receiving app checks it came from that storage.
 */
(function (global) {
  'use strict';

  var MAX_MS = 90000; // a minute and a half, then it stops itself

  function attach(root, opts) {
    opts = opts || {};
    var ltApi = (opts.ltApi || root.getAttribute('data-lt-api') || '').replace(/\/$/, '');
    var q = function (name) { return root.querySelector('[data-lt-video-' + name + ']'); };
    var statusEl = q('status');
    var preview = q('preview');
    // The field that carries the URL is often elsewhere in the form, not
    // inside the recorder — point at it with data-lt-video-url-target.
    var urlTarget = root.getAttribute('data-lt-video-url-target');
    var urlField = opts.urlField || (urlTarget && document.querySelector(urlTarget)) || q('url');
    var recBtn = q('record');
    var fileInput = q('file');
    var uploadBtn = q('upload');
    var recorder = null, chunks = [], stream = null;

    function say(text) { if (statusEl) statusEl.textContent = text; }

    function show(src) {
      if (!preview) return;
      preview.src = src;
      preview.style.display = 'block';
    }

    function upload(blob, filename) {
      say('Uploading video…');
      var fd = new FormData();
      fd.append('video', blob, filename || 'declaration.webm');
      return fetch(ltApi + '/api/video/upload', { method: 'POST', body: fd })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(function (d) {
          var url = d.videoUrl || d.url || '';
          if (urlField) urlField.value = url;
          say(url ? '✓ Video attached' : 'Upload failed. Try again.');
          if (url && opts.onUploaded) opts.onUploaded(url);
          return url;
        })
        .catch(function () {
          say('Upload failed. Nothing else is lost — try again in a minute.');
          return '';
        });
    }

    if (uploadBtn && fileInput) {
      uploadBtn.addEventListener('click', function () { fileInput.click(); });
    }
    if (fileInput) {
      fileInput.addEventListener('change', function () {
        if (!this.files.length) return;
        show(URL.createObjectURL(this.files[0]));
        upload(this.files[0], this.files[0].name);
      });
    }

    if (recBtn) {
      recBtn.addEventListener('click', function () {
        if (recorder && recorder.state === 'recording') { recorder.stop(); return; }
        navigator.mediaDevices.getUserMedia({ video: true, audio: true }).then(function (s) {
          stream = s; chunks = [];
          recorder = new MediaRecorder(s);
          recorder.ondataavailable = function (e) { if (e.data.size) chunks.push(e.data); };
          recorder.onstop = function () {
            stream.getTracks().forEach(function (t) { t.stop(); });
            var blob = new Blob(chunks, { type: 'video/webm' });
            show(URL.createObjectURL(blob));
            recBtn.textContent = recBtn.getAttribute('data-again') || '● Record again';
            upload(blob);
          };
          recorder.start();
          recBtn.textContent = recBtn.getAttribute('data-stop') || '■ Stop recording';
          say('Recording… click stop when done (keep it under a minute).');
          setTimeout(function () { if (recorder.state === 'recording') recorder.stop(); }, MAX_MS);
        }).catch(function () { say('Camera unavailable. Upload a file instead.'); });
      });
    }

    return { upload: upload };
  }

  global.LTVideo = { attach: attach };
})(window);
