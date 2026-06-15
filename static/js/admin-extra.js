/**
 * admin-extra.js
 * Inyectado en todas las páginas de SQLAdmin.
 * - Añade file picker (.jpg/.png) en el formulario de ProductImage
 * - Muestra preview de imagen en campos URL de ProductImage, Category y BlogPost
 */
(function () {
  'use strict';

  var path = window.location.pathname;

  // ─── File upload en ProductImage create / edit ───────────────────────────────
  var isProductImageForm =
    path.includes('/product-image/create') ||
    path.match(/\/product-image\/edit\//);

  if (isProductImageForm) {
    document.addEventListener('DOMContentLoaded', initProductImageUpload);
  }

  function initProductImageUpload() {
    var urlInput = document.querySelector('input[name="url"]');
    if (!urlInput) return;

    // Wrapper para el campo url
    var container = document.createElement('div');
    container.style.cssText = 'margin-top:6px;';

    // Fila: input de carpeta destino + botón subir
    var row = document.createElement('div');
    row.style.cssText = 'display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:6px;';

    // Input carpeta destino
    var destInput = document.createElement('input');
    destInput.type = 'text';
    destInput.className = 'form-control form-control-sm';
    destInput.placeholder = 'Carpeta destino, ej: products/Crema Pistacho Pura/200gr';
    destInput.value = 'products/misc';
    destInput.style.cssText = 'flex:1;min-width:220px;font-size:0.82em;';
    destInput.title = 'Carpeta donde se guardará la imagen dentro de static/images/';

    var destLabel = document.createElement('small');
    destLabel.textContent = 'Carpeta:';
    destLabel.style.cssText = 'color:#666;white-space:nowrap;';

    // Input de archivo oculto
    var fileInput = document.createElement('input');
    fileInput.type = 'file';
    fileInput.accept = '.jpg,.jpeg,.png';
    fileInput.style.display = 'none';

    // Botón
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn btn-secondary btn-sm';
    btn.innerHTML = '<i class="fas fa-upload me-1"></i>Seleccionar imagen (.jpg / .png)';
    btn.style.cssText = 'white-space:nowrap;';
    btn.onclick = function () { fileInput.click(); };

    // Spinner
    var spinner = document.createElement('small');
    spinner.textContent = 'Subiendo…';
    spinner.style.cssText = 'display:none;color:#888;';

    row.appendChild(destLabel);
    row.appendChild(destInput);
    row.appendChild(btn);
    row.appendChild(spinner);

    // Preview
    var preview = document.createElement('div');
    preview.style.cssText = 'margin-top:6px;';

    function showPreview(src) {
      if (!src) { preview.innerHTML = ''; return; }
      preview.innerHTML =
        '<img src="' + src + '" style="max-height:90px;max-width:180px;border-radius:4px;border:1px solid #ddd;object-fit:cover;" ' +
        'onerror="this.style.display=\'none\'">' +
        '<small style="display:block;color:#888;font-size:0.75em;margin-top:2px;">' + src + '</small>';
    }

    // Mostrar preview si ya hay URL (modo edición)
    showPreview(urlInput.value);
    urlInput.addEventListener('input', function () { showPreview(this.value); });

    // Upload al seleccionar archivo
    fileInput.addEventListener('change', function () {
      var file = this.files[0];
      if (!file) return;

      var ext = file.name.split('.').pop().toLowerCase();
      if (['jpg', 'jpeg', 'png'].indexOf(ext) === -1) {
        alert('Solo se permiten archivos .jpg y .png');
        this.value = '';
        return;
      }

      var dest = destInput.value.trim() || 'misc';
      var formData = new FormData();
      formData.append('file', file);
      formData.append('dest_path', dest);

      btn.disabled = true;
      spinner.style.display = 'inline';

      fetch('/admin-upload', { method: 'POST', body: formData })
        .then(function (r) {
          if (!r.ok) return r.json().then(function (d) { throw new Error(d.error || 'Error'); });
          return r.json();
        })
        .then(function (data) {
          urlInput.value = data.url;
          showPreview(data.url);
        })
        .catch(function (err) {
          alert('Error al subir la imagen: ' + err.message);
        })
        .finally(function () {
          btn.disabled = false;
          spinner.style.display = 'none';
          fileInput.value = '';
        });
    });

    container.appendChild(row);
    container.appendChild(fileInput);
    container.appendChild(preview);

    // Insertar después del input URL
    urlInput.parentNode.insertBefore(container, urlInput.nextSibling);
  }

  // ─── Preview inline en campos URL de otros formularios ───────────────────────
  // (Category.image_url, BlogPost.featured_image_url en edición)
  var isOtherForm =
    path.includes('/category/edit/') ||
    path.includes('/category/create') ||
    path.includes('/blog-post/edit/') ||
    path.includes('/blog-post/create');

  if (isOtherForm) {
    document.addEventListener('DOMContentLoaded', initUrlPreviews);
  }

  function initUrlPreviews() {
    var urlFields = ['image_url', 'featured_image_url'];
    urlFields.forEach(function (fieldName) {
      var input = document.querySelector('input[name="' + fieldName + '"]');
      if (!input) return;

      var preview = document.createElement('div');
      preview.style.cssText = 'margin-top:6px;';

      function update(src) {
        if (!src) { preview.innerHTML = ''; return; }
        preview.innerHTML =
          '<img src="' + src + '" style="max-height:80px;border-radius:4px;border:1px solid #ddd;" ' +
          'onerror="this.style.display=\'none\'">';
      }

      update(input.value);
      input.addEventListener('input', function () { update(this.value); });
      input.parentNode.insertBefore(preview, input.nextSibling);
    });
  }

})();
