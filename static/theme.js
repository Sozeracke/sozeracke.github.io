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
        var themeColor = document.querySelector('meta[name="theme-color"]');
        if (themeColor) {
            themeColor.setAttribute("content", theme === "light" ? "#f2efe8" : "#090c12");
        }
        document.querySelectorAll("[data-theme-toggle]").forEach(function (btn) {
            var isLight = theme === "light";
            btn.setAttribute("aria-pressed", isLight ? "true" : "false");
            btn.setAttribute("aria-label", isLight ? "Переключить на тёмную тему" : "Переключить на светлую тему");
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

    var navMenuToggle = document.querySelector("[data-nav-menu-toggle]");
    var navMenu = document.querySelector("[data-nav-menu]");

    function setNavMenu(open) {
        if (!navMenuToggle || !navMenu) return;
        navMenuToggle.setAttribute("aria-expanded", open ? "true" : "false");
        navMenu.classList.toggle("is-open", open);
        document.body.classList.toggle("nav-menu-open", open);
    }

    if (navMenuToggle && navMenu) {
        navMenuToggle.addEventListener("click", function () {
            setNavMenu(navMenuToggle.getAttribute("aria-expanded") !== "true");
        });

        navMenu.addEventListener("click", function (event) {
            if (event.target.closest("a")) setNavMenu(false);
        });

        document.addEventListener("click", function (event) {
            if (navMenu.classList.contains("is-open") &&
                !navMenu.contains(event.target) &&
                !navMenuToggle.contains(event.target)) {
                setNavMenu(false);
            }
        });
    }

    var catalogSidebar = document.querySelector("#catalog-sidebar");
    var catalogOpen = document.querySelector("[data-catalog-open]");
    var catalogCloseButtons = document.querySelectorAll("[data-catalog-close]");
    var catalogReturnFocus = null;

    function setCatalog(open) {
        if (!catalogSidebar || !catalogOpen) return;
        catalogSidebar.classList.toggle("is-open", open);
        document.body.classList.toggle("catalog-open", open);
        catalogOpen.setAttribute("aria-expanded", open ? "true" : "false");
        if (open) {
            catalogReturnFocus = document.activeElement;
            var closeButton = catalogSidebar.querySelector("[data-catalog-close]");
            if (closeButton) closeButton.focus();
        } else if (catalogReturnFocus && catalogReturnFocus.focus) {
            catalogReturnFocus.focus();
            catalogReturnFocus = null;
        }
    }

    if (catalogSidebar && catalogOpen) {
        catalogOpen.addEventListener("click", function () { setCatalog(true); });
        catalogCloseButtons.forEach(function (button) {
            button.addEventListener("click", function () { setCatalog(false); });
        });
        catalogSidebar.addEventListener("click", function (event) {
            if (event.target.closest("a") && window.matchMedia("(max-width: 900px)").matches) {
                setCatalog(false);
            }
        });
    }

    var pointerMotionQuery = window.matchMedia("(hover: hover) and (pointer: fine)");
    var reducedMotionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    var pointerMotionFrame = 0;
    var pointerX = 0;
    var pointerY = 0;
    var heroPointerX = 0;
    var heroPointerY = 0;
    var signalHero = document.querySelector(".signal-hero");

    function pointerMotionEnabled() {
        return pointerMotionQuery.matches && !reducedMotionQuery.matches;
    }

    function applyPointerMotion() {
        pointerMotionFrame = 0;
        root.style.setProperty("--pointer-x", pointerX.toFixed(3));
        root.style.setProperty("--pointer-y", pointerY.toFixed(3));
        root.style.setProperty("--hero-pointer-x", heroPointerX.toFixed(3));
        root.style.setProperty("--hero-pointer-y", heroPointerY.toFixed(3));
    }

    function schedulePointerMotion() {
        if (!pointerMotionFrame) {
            pointerMotionFrame = window.requestAnimationFrame(applyPointerMotion);
        }
    }

    function resetPointerMotion() {
        pointerX = 0;
        pointerY = 0;
        heroPointerX = 0;
        heroPointerY = 0;
        schedulePointerMotion();
    }

    if (pointerMotionEnabled()) {
        document.addEventListener("pointermove", function (event) {
            if (event.pointerType && event.pointerType !== "mouse") return;

            pointerX = Math.max(-1, Math.min(1, event.clientX / window.innerWidth * 2 - 1));
            pointerY = Math.max(-1, Math.min(1, event.clientY / window.innerHeight * 2 - 1));

            if (signalHero) {
                var heroRect = signalHero.getBoundingClientRect();
                var isInsideHero = event.clientX >= heroRect.left && event.clientX <= heroRect.right &&
                    event.clientY >= heroRect.top && event.clientY <= heroRect.bottom;

                if (isInsideHero) {
                    heroPointerX = Math.max(-1, Math.min(1, (event.clientX - heroRect.left) / heroRect.width * 2 - 1));
                    heroPointerY = Math.max(-1, Math.min(1, (event.clientY - heroRect.top) / heroRect.height * 2 - 1));
                } else {
                    heroPointerX = 0;
                    heroPointerY = 0;
                }
            }

            schedulePointerMotion();
        }, { passive: true });

        window.addEventListener("blur", resetPointerMotion);
    }

    reducedMotionQuery.addEventListener("change", function () {
        if (reducedMotionQuery.matches) resetPointerMotion();
    });

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
    lightbox.setAttribute("role", "dialog");
    lightbox.setAttribute("aria-modal", "true");
    lightbox.setAttribute("aria-label", "Просмотр изображения");
    lightbox.innerHTML =
        '<button type="button" class="lightbox-close" aria-label="Закрыть">×</button>' +
        '<img src="" alt="" class="lightbox-image">';
    document.body.appendChild(lightbox);

    var lightboxImg = lightbox.querySelector(".lightbox-image");
    var lightboxClose = lightbox.querySelector(".lightbox-close");
    var lightboxReturnFocus = null;

    function openLightbox(src, alt, source) {
        if (!src || !lightboxImg) return;
        lightboxImg.src = src;
        lightboxImg.alt = alt || "";
        lightboxReturnFocus = source || document.activeElement;
        lightbox.hidden = false;
        document.body.classList.add("lightbox-open");
        if (lightboxClose) lightboxClose.focus();
    }

    function closeLightbox() {
        lightbox.hidden = true;
        document.body.classList.remove("lightbox-open");
        if (lightboxImg) lightboxImg.src = "";
        if (lightboxReturnFocus && lightboxReturnFocus.focus) {
            lightboxReturnFocus.focus();
        }
        lightboxReturnFocus = null;
    }

    document.addEventListener("click", function (event) {
        var image = event.target.closest(".lightbox-image");
        if (image && !image.closest(".lightbox") && image.src) {
            event.preventDefault();
            openLightbox(image.src, image.alt, image);
            return;
        }
        if (event.target === lightbox || event.target === lightboxClose) {
            closeLightbox();
        }
    });

    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && !lightbox.hidden) {
            closeLightbox();
            return;
        }
        if (event.key === "Escape") {
            setNavMenu(false);
            setCatalog(false);
            return;
        }
        var image = event.target.closest && event.target.closest(".lightbox-image");
        if (image && !image.closest(".lightbox") &&
            (event.key === "Enter" || event.key === " ")) {
            event.preventDefault();
            openLightbox(image.src, image.alt, image);
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
