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