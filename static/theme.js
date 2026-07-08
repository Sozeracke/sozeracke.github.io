(function () {
    var STORAGE_KEY = "site-theme";
    var root = document.documentElement;

    function getPreferredTheme() {
        var stored = localStorage.getItem(STORAGE_KEY);
        if (stored === "light" || stored === "dark") {
            return stored;
        }
        return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
    }

    function applyTheme(theme) {
        root.setAttribute("data-theme", theme);
        document.querySelectorAll("[data-theme-toggle]").forEach(function (btn) {
            var isLight = theme === "light";
            btn.setAttribute("aria-pressed", isLight ? "true" : "false");
            btn.setAttribute("aria-label", isLight ? "Светлая тема" : "Тёмная тема");
            var icon = btn.querySelector(".theme-toggle-icon");
            if (icon) {
                icon.textContent = isLight ? "☀️" : "🌙";
            }
        });
    }

    function toggleTheme() {
        var next = root.getAttribute("data-theme") === "light" ? "dark" : "light";
        localStorage.setItem(STORAGE_KEY, next);
        applyTheme(next);
    }

    applyTheme(getPreferredTheme());

    document.addEventListener("click", function (event) {
        var btn = event.target.closest("[data-theme-toggle]");
        if (btn) {
            event.preventDefault();
            toggleTheme();
        }
    });

    var welcomeCard = document.getElementById("welcome-card");
    if (welcomeCard) {
        var welcomeKey = "welcome-dismissed";

        function dismissWelcomeCard() {
            welcomeCard.classList.add("is-hidden");
            localStorage.setItem(welcomeKey, "1");
        }

        if (localStorage.getItem(welcomeKey) === "1") {
            welcomeCard.classList.add("is-hidden");
        }

        document.addEventListener("click", function (event) {
            if (event.target.closest("[data-welcome-close]")) {
                event.preventDefault();
                dismissWelcomeCard();
                return;
            }
            if (event.target.closest("[data-welcome-close-soft]")) {
                dismissWelcomeCard();
            }
        });
    }

    document.querySelectorAll("[data-flash]").forEach(function (flash) {
        var closeBtn = flash.querySelector("[data-flash-close]");
        var hideFlash = function () {
            flash.classList.add("flash--hidden");
            window.setTimeout(function () {
                flash.remove();
            }, 220);
        };
        if (closeBtn) {
            closeBtn.addEventListener("click", hideFlash);
        }
        if (flash.getAttribute("data-flash-type") !== "error") {
            window.setTimeout(hideFlash, 5000);
        }
    });

    var lightbox = document.createElement("div");
    lightbox.className = "lightbox";
    lightbox.hidden = true;
    lightbox.innerHTML =
        '<button type="button" class="lightbox-close" aria-label="Закрыть">×</button>' +
        '<img src="" alt="" class="lightbox-image">';
    document.body.appendChild(lightbox);

    var lightboxImg = lightbox.querySelector(".lightbox-image");
    var lightboxClose = lightbox.querySelector(".lightbox-close");

    function openLightbox(src) {
        if (!src || !lightboxImg) return;
        lightboxImg.src = src;
        lightbox.hidden = false;
        document.body.classList.add("lightbox-open");
    }

    function closeLightbox() {
        lightbox.hidden = true;
        document.body.classList.remove("lightbox-open");
        if (lightboxImg) lightboxImg.src = "";
    }

    document.addEventListener("click", function (event) {
        var image = event.target.closest(".lightbox-image");
        if (image && !image.closest(".lightbox") && image.src) {
            event.preventDefault();
            openLightbox(image.src);
            return;
        }
        if (event.target === lightbox || event.target === lightboxClose) {
            closeLightbox();
        }
    });

    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && !lightbox.hidden) {
            closeLightbox();
        }
    });

    function setShareButtonState(button, text, copied) {
        var label = button.querySelector(".share-card-label");
        if (!label) return;
        var original = button.getAttribute("data-share-original") || label.textContent;
        button.setAttribute("data-share-original", original);
        label.textContent = text;
        button.classList.toggle("share-card-button--copied", copied);
        window.setTimeout(function () {
            label.textContent = original;
            button.classList.remove("share-card-button--copied");
        }, 1600);
    }

    document.addEventListener("click", function (event) {
        var shareButton = event.target.closest("[data-share-post]");
        if (!shareButton) {
            return;
        }
        event.preventDefault();
        var shareUrl = shareButton.getAttribute("data-share-url") || window.location.href;
        var shareTitle = shareButton.getAttribute("data-share-title") || document.title;

        if (navigator.share) {
            navigator.share({
                title: shareTitle,
                url: shareUrl,
            }).catch(function () {});
            return;
        }

        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(shareUrl).then(function () {
                setShareButtonState(shareButton, "Скопировано", true);
            }).catch(function () {
                setShareButtonState(shareButton, "Ссылка готова", true);
            });
        } else {
            setShareButtonState(shareButton, "Ссылка готова", true);
        }
    });

    var readingProgress = document.querySelector("[data-reading-progress]");
    var article = document.querySelector(".post-full");

    function updateReadingProgress() {
        if (!readingProgress || !article) {
            return;
        }
        var start = article.offsetTop;
        var end = article.offsetTop + article.scrollHeight - window.innerHeight;
        var progress = end > start ? (window.scrollY - start) / (end - start) : 0;
        progress = Math.max(0, Math.min(1, progress));
        readingProgress.style.transform = "scaleX(" + progress + ")";
    }

    if (readingProgress && article) {
        updateReadingProgress();
        window.addEventListener("scroll", updateReadingProgress, { passive: true });
        window.addEventListener("resize", updateReadingProgress);
    }

    document.querySelectorAll(".like-form").forEach(function (form) {
        form.addEventListener("submit", function (event) {
            event.preventDefault();
            var button = form.querySelector(".like-btn");
            if (!button || button.disabled) {
                return;
            }
            button.disabled = true;
            fetch(form.action, {
                method: "POST",
                headers: { "X-Requested-With": "XMLHttpRequest" },
            })
                .then(function (response) {
                    return response.json();
                })
                .then(function (data) {
                    button.classList.toggle("like-btn--active", data.liked);
                    var countEl = form.querySelector(".like-btn-count");
                    if (countEl) {
                        countEl.textContent = data.like_count;
                    }
                })
                .catch(function () {
                    form.submit();
                })
                .finally(function () {
                    button.disabled = false;
                });
        });
    });
})();
