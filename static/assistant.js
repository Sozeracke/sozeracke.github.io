(function () {
    var root = document.querySelector("[data-assistant]");
    if (!root || root.getAttribute("data-assistant-enabled") !== "true") return;

    var form = root.querySelector("[data-assistant-form]");
    var input = root.querySelector("[data-assistant-input]");
    var messages = root.querySelector("[data-assistant-messages]");
    var send = root.querySelector("[data-assistant-send]");
    var note = root.querySelector("[data-assistant-note]");
    if (!form || !input || !messages || !send) return;

    function setNote(text, isError) {
        if (!note) return;
        note.textContent = text;
        note.classList.toggle("is-error", Boolean(isError));
    }

    function scrollToLastMessage() {
        window.requestAnimationFrame(function () {
            messages.scrollTop = messages.scrollHeight;
        });
    }

    function createMessage(type, text, sources) {
        var article = document.createElement("article");
        article.className = "assistant-message assistant-message--" + type;

        if (type === "bot") {
            var avatar = document.createElement("div");
            avatar.className = "assistant-message-avatar";
            avatar.setAttribute("aria-hidden", "true");
            avatar.textContent = "✦";
            article.appendChild(avatar);
        }

        var body = document.createElement("div");
        body.className = "assistant-message-body";
        var label = document.createElement("span");
        label.textContent = type === "bot" ? "SOZERACKE AI" : "ВАШ ВОПРОС";
        body.appendChild(label);
        var paragraph = document.createElement("p");
        paragraph.textContent = text;
        body.appendChild(paragraph);

        if (sources && sources.length) {
            var sourcesWrap = document.createElement("div");
            sourcesWrap.className = "assistant-sources";
            var sourcesTitle = document.createElement("b");
            sourcesTitle.textContent = "Материалы в ответе";
            sourcesWrap.appendChild(sourcesTitle);
            var list = document.createElement("div");
            list.className = "assistant-source-list";

            sources.forEach(function (source) {
                if (!source || !source.url || !source.title) return;
                var link = document.createElement("a");
                link.href = source.url;
                link.textContent = source.title;
                link.title = source.category || "Материал";
                list.appendChild(link);
            });
            if (list.childElementCount) {
                sourcesWrap.appendChild(list);
                body.appendChild(sourcesWrap);
            }
        }

        article.appendChild(body);
        messages.appendChild(article);
        scrollToLastMessage();
        return article;
    }

    function createTypingMessage() {
        var article = document.createElement("article");
        article.className = "assistant-message assistant-message--bot assistant-message--typing";
        article.setAttribute("aria-label", "Помощник готовит ответ");
        article.innerHTML = '<div class="assistant-message-avatar" aria-hidden="true">✦</div><div class="assistant-typing-dots" aria-hidden="true"><i></i><i></i><i></i></div>';
        messages.appendChild(article);
        scrollToLastMessage();
        return article;
    }

    function resizeInput() {
        input.style.height = "auto";
        input.style.height = Math.min(input.scrollHeight, 160) + "px";
    }

    input.addEventListener("input", resizeInput);
    input.addEventListener("keydown", function (event) {
        if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            form.requestSubmit();
        }
    });

    root.querySelectorAll("[data-assistant-suggestion]").forEach(function (button) {
        button.addEventListener("click", function () {
            input.value = button.textContent.trim();
            resizeInput();
            input.focus();
        });
    });

    form.addEventListener("submit", async function (event) {
        event.preventDefault();
        var message = input.value.trim();
        if (message.length < 2) {
            setNote("Напишите вопрос чуть подробнее.", true);
            input.focus();
            return;
        }

        createMessage("user", message);
        input.value = "";
        resizeInput();
        send.disabled = true;
        form.classList.add("is-loading");
        setNote("Ищу в материалах журнала и собираю ответ…", false);
        var typing = createTypingMessage();

        try {
            var response = await fetch("/api/assistant", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ message: message })
            });
            var payload = await response.json().catch(function () { return {}; });
            if (!response.ok || !payload.answer) {
                throw new Error(payload.error || "Не удалось получить ответ. Попробуйте ещё раз немного позже.");
            }
            createMessage("bot", payload.answer, payload.sources || []);
            if (payload.degraded) {
                setNote("Генерация временно недоступна — показаны подходящие материалы журнала.", false);
            } else {
                setNote("Ответ сформирован. Источники под ним ведут к исходным материалам.", false);
            }
        } catch (error) {
            createMessage("bot", error && error.message ? error.message : "Не удалось получить ответ. Попробуйте ещё раз немного позже.");
            setNote("Запрос не отправлен. Проверьте соединение и попробуйте ещё раз.", true);
        } finally {
            if (typing) typing.remove();
            send.disabled = false;
            form.classList.remove("is-loading");
            input.focus();
        }
    });
}());
