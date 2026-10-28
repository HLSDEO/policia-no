/* Provedores de IA de chat: a lista, os presets de URL/modelo e os utilitários dos <select>
 * "escolha um ou digite o seu".
 *
 * Vive fora de config-tabs.js porque são DUAS telas que escolhem provedor de IA e precisam
 * oferecer exatamente as mesmas opções: Configurações (o padrão da instalação, gravado no .env) e
 * Usuários → "IA do usuário" (a exceção por pessoa, gravada no banco). Preset novo entra aqui e
 * aparece nas duas.
 *
 * Expõe window.IAPresets.
 */
(function (window) {
  "use strict";

  var OUTRO_VALUE = "__outro__";

  // ---------- presets de provedores ----------

  // IA de chat (Prompt IA e Comentário do relatório usam o mesmo conjunto)
  var CHAT_PROVIDERS = [
    { value: "openai", label: "OpenAI" },
    { value: "openai-cert", label: "OpenAI — com certificado" },
    { value: "anthropic", label: "Anthropic" },
    { value: "google", label: "Google Gemini" },
    { value: "azure-openai", label: "Azure OpenAI" },
    { value: "deepseek", label: "DeepSeek" },
    { value: "ollama", label: "Ollama (local, offline)" },
    { value: "lmstudio", label: "LM Studio (local, offline)" },
    { value: "outro", label: "Outro / compatível com OpenAI" }
  ];

  var CHAT_PRESETS = {
    "openai": {
      urls: ["https://api.openai.com/v1"],
      models: ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "o3", "o3-mini"],
      embModels: ["text-embedding-3-small", "text-embedding-3-large", "text-embedding-ada-002"]
    },
    // Gateway OpenAI-compatível servido por HTTPS com certificado próprio (LiteLLM da SR/RR):
    // a URL tem que ser a mesma que o certificado cobre — o SAN é o IP, não um nome DNS.
    "openai-cert": {
      urls: ["https://10.95.7.40:8080/v1"],
      models: ["auto", "text-model", "image-model", "video-model", "docs-model"],
      embModels: []
    },
    "anthropic": {
      urls: ["https://api.anthropic.com/v1"],
      models: ["claude-sonnet-5", "claude-opus-4-8", "claude-haiku-4-5", "claude-fable-5"],
      embModels: []
    },
    "google": {
      urls: ["https://generativelanguage.googleapis.com/v1beta"],
      models: ["gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash"],
      embModels: ["text-embedding-004", "text-multilingual-embedding-002"]
    },
    "azure-openai": {
      urls: [],
      models: ["gpt-4o", "gpt-4o-mini", "gpt-35-turbo"],
      embModels: ["text-embedding-3-small", "text-embedding-3-large"]
    },
    "deepseek": {
      urls: ["https://api.deepseek.com"],
      models: ["deepseek-v4-flash", "deepseek-v4-pro"],
      embModels: []
    },
    "ollama": {
      // 1ª URL: serviço "ollama" do docker-compose (a API roda em container);
      // a 2ª serve para Ollama instalado direto no host.
      urls: ["http://ollama:11434/v1", "http://host.docker.internal:11434/v1"],
      models: ["qwen3:30b-a3b", "qwen3-next:80b-a3b", "qwen3.8-flash-next", "dengcao/Qwen3-30B-A3B:Q3_K_M"],
      embModels: ["nomic-embed-text", "mxbai-embed-large", "bge-m3"]
    },
    "lmstudio": {
      urls: ["http://host.docker.internal:1234/v1", "http://localhost:1234/v1"],
      models: ["qwen/qwen3-14b", "openai/gpt-oss-20b", "mistralai/mistral-small-3.2", "qwen/qwen3-8b", "meta-llama-3.1-8b-instruct"],
      embModels: ["text-embedding-nomic-embed-text-v1.5", "text-embedding-bge-m3"]
    },
    "outro": { urls: [], models: [], embModels: [] }
  };

  // ---------- utilitários dos <select> ----------

  // Preenche um <select> com as opções + "Outro (digitar)".
  // Se o valor atual não estiver nas opções, seleciona "Outro" e mostra o campo de texto.
  function populateSelect($select, $customInput, options, currentValue) {
    $select.empty();
    options.forEach(function (opt) {
      $select.append($("<option>").val(opt).text(opt));
    });
    $select.append($("<option>").val(OUTRO_VALUE).text("Outro (digitar)"));

    if (currentValue && options.indexOf(currentValue) !== -1) {
      $select.val(currentValue);
      $customInput.addClass("hidden").val("");
    } else if (currentValue) {
      $select.val(OUTRO_VALUE);
      $customInput.removeClass("hidden").val(currentValue);
    } else {
      $select.val(options.length ? options[0] : OUTRO_VALUE);
      $customInput.toggleClass("hidden", $select.val() !== OUTRO_VALUE);
    }
  }

  function currentValue($select, $customInput) {
    return $select.val() === OUTRO_VALUE ? $.trim($customInput.val()) : $select.val();
  }

  function montarSelectProvedores($select, provedores) {
    $select.empty();
    provedores.forEach(function (p) {
      $select.append($("<option>").val(p.value).text(p.label));
    });
  }

  // ---------- olho do campo de chave ----------

  // Delegado no document: vale para qualquer botão .password-toggle[data-toggle-key], nas duas
  // telas. Fica aqui, e não em cada uma, porque dois handlers na mesma página se anulariam — o
  // segundo desfaria a troca de type feita pelo primeiro.
  $(document).on("click", ".password-toggle[data-toggle-key]", function () {
    var $input = $("#" + $(this).attr("data-toggle-key"));
    var escondida = $input.attr("type") === "password";
    $input.attr("type", escondida ? "text" : "password");
    $(this).attr("title", escondida ? "Ocultar chave" : "Mostrar chave");
  });

  window.IAPresets = {
    OUTRO: OUTRO_VALUE,
    CHAT_PROVIDERS: CHAT_PROVIDERS,
    CHAT_PRESETS: CHAT_PRESETS,
    popularSelect: populateSelect,
    valorAtual: currentValue,
    montarSelectProvedores: montarSelectProvedores
  };
})(window);
