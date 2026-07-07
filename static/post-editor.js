(function () {
    var placeholderBtn = document.getElementById("insert-image-placeholder");
    var content = document.getElementById("content");
    var fileInput = document.getElementById("content_images");
    var preview = document.getElementById("content-images-preview");

    if (placeholderBtn && content) {
        placeholderBtn.addEventListener("click", function () {
            insertAtCursor(content, "[[IMAGE]]");
            content.focus();
        });
    }

    if (fileInput && preview) {
        fileInput.addEventListener("change", function () {
            preview.innerHTML = "";
            if (!fileInput.files.length) {
                preview.hidden = true;
                return;
            }
            preview.hidden = false;
            Array.prototype.forEach.call(fileInput.files, function (file) {
                var item = document.createElement("li");
                item.textContent = file.name;
                preview.appendChild(item);
            });
        });
    }

    function insertAtCursor(textarea, text) {
        var start = textarea.selectionStart;
        var end = textarea.selectionEnd;
        var value = textarea.value;
        textarea.value = value.slice(0, start) + text + value.slice(end);
        var cursor = start + text.length;
        textarea.selectionStart = cursor;
        textarea.selectionEnd = cursor;
    }
})();