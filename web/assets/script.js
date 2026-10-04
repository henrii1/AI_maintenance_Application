// EQUITHEUS / ONESI — shared behavior
document.addEventListener('DOMContentLoaded', function () {
  // Sticky nav shadow on scroll
  var nav = document.querySelector('.nav');
  if (nav) {
    var onScroll = function () {
      if (window.scrollY > 8) nav.classList.add('is-scrolled');
      else nav.classList.remove('is-scrolled');
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    onScroll();
  }

  // Mobile nav toggle
  var toggle = document.querySelector('.nav-toggle');
  var links = document.querySelector('.nav-links');
  if (toggle && links) {
    toggle.addEventListener('click', function () {
      links.classList.toggle('open');
      var expanded = links.classList.contains('open');
      toggle.setAttribute('aria-expanded', expanded);
    });
    links.querySelectorAll('a').forEach(function (a) {
      a.addEventListener('click', function () { links.classList.remove('open'); });
    });
  }

  // Reveal-on-scroll (single, quiet pattern)
  var revealEls = document.querySelectorAll('.reveal');
  if ('IntersectionObserver' in window && revealEls.length) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add('in');
          io.unobserve(entry.target);
        }
      });
    }, { threshold: 0.12, rootMargin: '0px 0px -40px 0px' });
    revealEls.forEach(function (el) { io.observe(el); });
  } else {
    revealEls.forEach(function (el) { el.classList.add('in'); });
  }

  // Demo request / early access / contact forms.
  // Forms marked data-access-form create a real demo access request; others keep placeholder behaviour.
  document.querySelectorAll('form[data-form]').forEach(function (form) {
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var success = form.parentElement.querySelector('.form-success') || form.querySelector('.form-success');
      var btn = form.querySelector('button[type="submit"]');
      function done() {
        form.querySelectorAll('input, textarea, select').forEach(function (f) { f.disabled = true; });
        if (btn) { btn.disabled = true; btn.textContent = 'Submitted'; }
        if (success) success.classList.add('show');
      }
      if (!form.hasAttribute('data-access-form')) { done(); return; }
      var data = new FormData(form);
      var msg = form.querySelector('.form-msg');
      if (!msg) {
        msg = document.createElement('p');
        msg.className = 'form-msg';
        msg.setAttribute('role', 'alert');
        msg.style.cssText = 'color:#b42318; font-size:13.5px; margin-top:10px;';
        form.appendChild(msg);
      }
      msg.textContent = '';
      var label = btn ? btn.textContent : '';
      if (btn) btn.disabled = true;
      function fail(text) { msg.textContent = text; if (btn) { btn.disabled = false; btn.textContent = label; } }
      fetch('/api/access/request', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: data.get('email'), name: data.get('name'), company: data.get('company'),
                               note: data.get('title') ? 'Job title: ' + data.get('title') : '' })
      }).then(function (r) {
        if (r.ok) { done(); return; }
        return r.json().catch(function () { return {}; }).then(function (j) {
          fail(typeof j.detail === 'string' ? j.detail : 'Please check the form and try again.');
        });
      }).catch(function () { fail('Network error, please try again.'); });
    });
  });
});
