/* Configurações em abas: Prompt IA, Busca semântica, OCR, Comparação de imagens,
 * Comentário IA no relatório, Análise de documentos e Resumo noturno — cada uma com formulário,
 * endpoint e provedores próprios
 * (o .env guarda as credenciais de cada seção em variáveis separadas).
 *
 * Os endpoints são os da rota /api/config/* em rotas/configuracao_admin.py:
 *   /api/config/ai/prompt         AI_PROVIDER/AI_MODEL/AI_API_URL/AI_API_KEY/AI_CONTEXT_LENGTH
 *   /api/config/ai/embeddings     EMBED_MODE/EMBED_PROVIDER/EMBED_API_URL/EMBED_API_KEY/EMBED_MODEL
 *   /api/config/ai/rag            RAG_PESO_SEMANTICO/RAG_PESO_TEXTUAL/RAG_PESO_GRAFO/RAG_LIMITE
 *   /api/config/ocr               OCR_MODE/OCR_PROVIDER/OCR_API_URL/OCR_API_KEY/OCR_MODEL/OCR_IDIOMA/OCR_PSM
 *   /api/config/image-comparison  IMAGE_CMP_MODE/IMAGE_CMP_PROVIDER/IMAGE_CMP_API_URL/IMAGE_CMP_API_KEY/IMAGE_CMP_MODEL
 *   /api/config/ai/report         REPORT_AI_PROVIDER/REPORT_AI_MODEL/REPORT_AI_API_URL/REPORT_AI_API_KEY
 *   /api/config/ai/document       DOC_AI_PROVIDER/DOC_AI_MODEL/DOC_AI_API_URL/DOC_AI_API_KEY
 *   /api/config/ai/resumo-noturno RESUMO_NOTURNO_PROVIDER/_MODEL/_API_URL/_API_KEY
 */
(function () {
  "use strict";

  // Provedores, presets e utilitários dos <select> — compartilhados com a tela Usuários
  // ("IA do usuário"), ver js/utils/ia_presets.js.
  var OUTRO_VALUE = IAPresets.OUTRO;
  var CHAT_PROVIDERS = IAPresets.CHAT_PROVIDERS;
  var CHAT_PRESETS = IAPresets.CHAT_PRESETS;
  var populateSelect = IAPresets.popularSelect;
  var currentValue = IAPresets.valorAtual;
  var montarSelectProvedores = IAPresets.montarSelectProvedores;

  // Modelos de embeddings do Ollama (modo local) — independem do provedor de chat.
  var LOCAL_EMB_MODELS = ["nomic-embed-text", "bge-m3", "mxbai-embed-large", "all-minilm"];

  // API de visão — OCR (o modelo só é usado pelo Qwen; o Google Vision não recebe modelo)
  var VISAO_OCR = {
    "google-vision": {
      label: "Google Vision",
      urls: ["https://vision.googleapis.com/v1"],
      models: [],
      usaModelo: false,
      ajuda: "Google Cloud Vision — a chave vai como parâmetro ?key=. Texto extraído com TEXT_DETECTION."
    },
    "qwen-vision": {
      label: "Qwen com visão",
      urls: ["https://dashscope.aliyuncs.com/compatible-mode/v1"],
      models: ["qwen-vl-max", "qwen-vl-plus", "qwen2.5-vl-72b-instruct"],
      usaModelo: true,
      ajuda: "API compatível com OpenAI (ex.: DashScope). A imagem é enviada em base64 junto com o pedido de extração de texto."
    },
    "outro": {
      label: "Outro / compatível com OpenAI",
      urls: [],
      models: [],
      usaModelo: true,
      ajuda: "API compatível com OpenAI."
    }
  };

  // API de visão — comparação de imagens (o Google aqui é o Gemini, que compara as duas fotos)
  var VISAO_CMP = {
    "google-vision": {
      label: "Google Vision / Gemini",
      urls: ["https://generativelanguage.googleapis.com/v1beta"],
      models: ["gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash"],
      usaModelo: true,
      ajuda: "Google Gemini — a chave vai como parâmetro ?key=. Referência e candidata são enviadas juntas."
    },
    "qwen-vision": {
      label: "Qwen com visão",
      urls: ["https://dashscope.aliyuncs.com/compatible-mode/v1"],
      models: ["qwen-vl-max", "qwen-vl-plus", "qwen2.5-vl-72b-instruct"],
      usaModelo: true,
      ajuda: "API compatível com OpenAI (ex.: DashScope). Referência e candidata são enviadas juntas."
    },
    "outro": {
      label: "Outro / compatível com OpenAI",
      urls: [],
      models: [],
      usaModelo: true,
      ajuda: "API compatível com OpenAI."
    }
  };

  // ---------- utilitários ----------

  function setStatus($el, text, kind) {
    $el.text(text).removeClass("success error").addClass(kind || "");
  }

  // ---------- certificado do gateway (um por aba) ----------

  // Cada aba tem o seu certificado no .env (AI_CA_CERT, EMBED_CA_CERT, REPORT_AI_CA_CERT,
  // DOC_AI_CA_CERT, RESUMO_NOTURNO_CA_CERT) — abas diferentes podem apontar para gateways
  // diferentes. O back-end nunca
  // devolve o PEM, só a descrição (nome, validade, impressão digital): o campo fica sempre vazio e
  // "em branco ao salvar" mantém o que está gravado, igual à chave de API.
  function Certificado(prefixo) {
    var campo = function (sufixo) { return $("#" + prefixo + "-ca-cert" + sufixo); };
    var cert = {
      remover: false,
      ajudaPadrao: "",
      // Visibilidade: o certificado só faz sentido no provedor com gateway próprio.
      alternar: function (visivel) {
        $("#" + prefixo + "-ca-cert-wrap").toggleClass("hidden", !visivel);
      },
      mostrar: function (resumo) {
        cert.remover = false;
        campo("").val("");
        campo("-file").val("");
        campo("-remover").toggleClass("hidden", !resumo);
        campo("-help").text(resumo
          ? "Certificado salvo: " + resumo + ". Deixe em branco para mantê-lo."
          : cert.ajudaPadrao);
      },
      payload: function () {
        return { caCert: $.trim(campo("").val()), removerCaCert: cert.remover };
      },
      ligar: function () {
        // o texto de ajuda padrão é o que está no HTML da aba (cada uma tem o seu)
        cert.ajudaPadrao = campo("-help").text();
        // arquivo .crt escolhido: o conteúdo vai para o textarea (é texto PEM), e daí para o POST —
        // o certificado é público, mas assim segue o mesmo caminho de qualquer outro campo do form
        campo("-file").on("change", function () {
          var arquivo = this.files && this.files[0];
          if (!arquivo) return;
          var leitor = new FileReader();
          leitor.onload = function () {
            campo("").val(String(leitor.result || ""));
            cert.remover = false;
          };
          leitor.onerror = function () { Dialogo.alerta("Não foi possível ler o arquivo do certificado."); };
          leitor.readAsText(arquivo);
        });
        campo("-remover").on("click", function () {
          cert.remover = true;
          campo("").val("");
          campo("-file").val("");
          campo("-help").text("O certificado será apagado ao salvar.");
        });
        return cert;
      }
    };
    return cert;
  }

  var CERTIFICADOS = {
    prompt: Certificado("prompt").ligar(),
    emb: Certificado("emb").ligar(),
    report: Certificado("report").ligar(),
    doc: Certificado("doc").ligar(),
    resumo: Certificado("resumo").ligar()
  };

  // Esforço de raciocínio: mesma ideia — campo por aba, só visível no provedor com gateway próprio.
  function alternarRaciocinio(prefixo, visivel) {
    $("#" + prefixo + "-reasoning-wrap").toggleClass("hidden", !visivel);
  }

  var ICONE_SALVAR = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>';

  // ---------- abas ----------

  var CHAVE_ABA = "analyser-config-aba";
  var secoesCarregadas = {};

  function switchTab(nome) {
    $(".extracao-tab[data-tab]").each(function () {
      var ativo = $(this).attr("data-tab") === nome;
      $(this).toggleClass("active", ativo).attr("aria-selected", ativo ? "true" : "false");
    });
    $(".extracao-tab-panel").each(function () {
      $(this).toggleClass("hidden", this.id !== "panel-" + nome);
    });
    try { localStorage.setItem(CHAVE_ABA, nome); } catch (e) { /* sem persistência */ }
    if (!secoesCarregadas[nome]) {
      secoesCarregadas[nome] = true;
      SECOES[nome].carregar();
    }
  }

  // ---------- aba: Prompt IA ----------

  var PromptIA = {
    endpoint: "/api/config/ai/prompt",
    carregar: function () {
      var $status = $('[data-status-for="prompt-config-form"]');
      $.getJSON(this.endpoint)
        .done(function (data) {
          data = data || {};
          $("#prompt-provider").val(data.provider || "openai");
          $("#prompt-context-length").val(data.contextLength ? data.contextLength : "");
          $("#prompt-timeout").val(data.timeout ? data.timeout : "");
          $("#prompt-reasoning").val(data.reasoningEffort || "low");
          $("#prompt-anonimizar").prop("checked", !!data.anonimizar);
          PromptIA.refreshFields(data.model, data.apiUrl);
          CERTIFICADOS.prompt.mostrar(data.caCertResumo);
          if (data.apiKeyMasked) {
            $("#prompt-api-key").attr("placeholder", data.apiKeyMasked);
            $("#prompt-api-key-help").text("Uma chave já está salva. Deixe em branco para mantê-la.");
          }
        })
        .fail(function () {
          PromptIA.refreshFields("", "");
          setStatus($status, "Não foi possível carregar a configuração atual.", "error");
        });
    },
    refreshFields: function (currentModel, currentUrl) {
      var preset = CHAT_PRESETS[$("#prompt-provider").val()] || CHAT_PRESETS.outro;
      populateSelect($("#prompt-model"), $("#prompt-model-custom"), preset.models, currentModel);
      populateSelect($("#prompt-api-url"), $("#prompt-api-url-custom"), preset.urls, currentUrl);
      $("#prompt-model-pull").toggleClass("hidden", $("#prompt-provider").val() !== "ollama");
      $("#prompt-model-ajuda").toggleClass("hidden", $("#prompt-provider").val() !== "ollama");
      // o tamanho do contexto vale para TODO provedor (é a régua dos cortes de tamanho do turno,
      // ver funcoes/ia_provedores.py); no Ollama ele também vira o num_ctx da requisição
      $("#prompt-context-length-ajuda").text(
        $("#prompt-provider").val() === "ollama"
          ? "Vai como num_ctx em cada requisição e é a régua que o sistema usa para cortar buscas grandes. Modelos locais vêm com um contexto pequeno por padrão (o suficiente pra travar a IA no meio de uma busca). Em branco usa o padrão do servidor Ollama. Valores maiores usam mais memória da GPU."
          : "Quanto o modelo aceita receber de uma vez. É a régua que o sistema usa para cortar buscas grandes e compactar a conversa antes de chamar a IA — quando o valor está errado (ou o modelo tem nome genérico, como no gateway), o sistema assume 32 mil tokens e envia menos do que poderia. Em branco, o valor é deduzido do nome do modelo."
      );
      var comCertificado = $("#prompt-provider").val() === "openai-cert";
      CERTIFICADOS.prompt.alternar(comCertificado);
      alternarRaciocinio("prompt", comCertificado);
    },
    salvar: function () {
      return $.extend({
        provider: $("#prompt-provider").val(),
        model: currentValue($("#prompt-model"), $("#prompt-model-custom")),
        apiUrl: currentValue($("#prompt-api-url"), $("#prompt-api-url-custom")),
        apiKey: $("#prompt-api-key").val(),
        contextLength: parseInt($("#prompt-context-length").val(), 10) || 0,
        timeout: parseInt($("#prompt-timeout").val(), 10) || 0,
        anonimizar: $("#prompt-anonimizar").is(":checked"),
        reasoningEffort: $("#prompt-reasoning").val()
      }, CERTIFICADOS.prompt.payload());
    }
  };

  // ---------- aba: Busca semântica ----------

  var BuscaSemantica = {
    endpoint: "/api/config/ai/embeddings",
    carregar: function () {
      var $status = $('[data-status-for="embeddings-config-form"]');
      $.getJSON(this.endpoint)
        .done(function (data) {
          data = data || {};
          $("#emb-mode").val(data.mode === "local" ? "local" : "api");
          $("#emb-provider").val(data.provider || "openai");
          BuscaSemantica.refreshFields(data.model, data.apiUrl);
          CERTIFICADOS.emb.mostrar(data.caCertResumo);
          if (data.apiKeyMasked) {
            $("#emb-api-key").attr("placeholder", data.apiKeyMasked);
            $("#emb-api-key-help").text("Uma chave já está salva. Deixe em branco para mantê-la.");
          }
          BuscaSemantica.refreshVisibilidade();
        })
        .fail(function () {
          BuscaSemantica.refreshFields("", "");
          setStatus($status, "Não foi possível carregar a configuração atual.", "error");
        });
    },
    embModelOptions: function () {
      if ($("#emb-mode").val() === "local") return LOCAL_EMB_MODELS;
      var preset = CHAT_PRESETS[$("#emb-provider").val()] || CHAT_PRESETS.outro;
      return preset.embModels;
    },
    refreshFields: function (currentEmbModel, currentUrl) {
      populateSelect($("#emb-model"), $("#emb-model-custom"), BuscaSemantica.embModelOptions(), currentEmbModel);
      var preset = CHAT_PRESETS[$("#emb-provider").val()] || CHAT_PRESETS.outro;
      populateSelect($("#emb-api-url"), $("#emb-api-url-custom"), preset.urls, currentUrl);
      BuscaSemantica.refreshVisibilidade();
    },
    refreshVisibilidade: function () {
      var api = $("#emb-mode").val() === "api";
      $("#emb-provider-wrap").toggleClass("hidden", !api);
      $("#emb-api-url-wrap").toggleClass("hidden", !api);
      $("#emb-api-key-wrap").toggleClass("hidden", !api);
      $("#emb-pull").toggleClass("hidden", api);
      // no modo local não há TLS a validar (o Ollama do servidor responde em HTTP)
      CERTIFICADOS.emb.alternar(api && $("#emb-provider").val() === "openai-cert");
    },
    salvar: function () {
      return $.extend({
        mode: $("#emb-mode").val(),
        provider: $("#emb-provider").val(),
        apiUrl: currentValue($("#emb-api-url"), $("#emb-api-url-custom")),
        apiKey: $("#emb-api-key").val(),
        model: currentValue($("#emb-model"), $("#emb-model-custom"))
      }, CERTIFICADOS.emb.payload());
    }
  };

  // ---------- aba: RAG híbrido ----------

  var RagHibrido = {
    endpoint: "/api/config/ai/rag",
    carregar: function () {
      var $status = $('[data-status-for="rag-config-form"]');
      $.getJSON(this.endpoint)
        .done(function (data) {
          data = data || {};
          $("#rag-peso-semantico").val(data.pesoSemantico != null ? data.pesoSemantico : 0.5);
          $("#rag-peso-textual").val(data.pesoTextual != null ? data.pesoTextual : 0.3);
          $("#rag-peso-grafo").val(data.pesoGrafo != null ? data.pesoGrafo : 0.2);
          $("#rag-limite").val(data.limite || 15);
        })
        .fail(function () {
          setStatus($status, "Não foi possível carregar a configuração atual.", "error");
        });
    },
    salvar: function () {
      return {
        pesoSemantico: parseFloat($("#rag-peso-semantico").val()) || 0,
        pesoTextual: parseFloat($("#rag-peso-textual").val()) || 0,
        pesoGrafo: parseFloat($("#rag-peso-grafo").val()) || 0,
        limite: parseInt($("#rag-limite").val(), 10) || 15
      };
    }
  };

  // ---------- aba: OCR ----------

  var OCR = {
    endpoint: "/api/config/ocr",
    carregar: function () {
      var $status = $('[data-status-for="ocr-config-form"]');
      $.getJSON(this.endpoint)
        .done(function (data) {
          data = data || {};
          $("#ocr-mode").val(data.mode === "api" ? "api" : "local");
          $("#ocr-provider").val(data.provider || "google-vision");
          $("#ocr-idioma").val(data.idioma || "por+eng");
          $("#ocr-psm").val(data.psm || "3");
          OCR.refreshFields(data.model, data.apiUrl);
          if (data.apiKeyMasked) {
            $("#ocr-api-key").attr("placeholder", data.apiKeyMasked);
            $("#ocr-api-key-help").text("Uma chave já está salva. Deixe em branco para mantê-la.");
          }
          OCR.refreshVisibilidade();
        })
        .fail(function () {
          OCR.refreshFields("", "");
          setStatus($status, "Não foi possível carregar a configuração atual.", "error");
        });
    },
    refreshFields: function (currentModel, currentUrl) {
      var preset = VISAO_OCR[$("#ocr-provider").val()] || VISAO_OCR.outro;
      populateSelect($("#ocr-model"), $("#ocr-model-custom"), preset.models, currentModel);
      populateSelect($("#ocr-api-url"), $("#ocr-api-url-custom"), preset.urls, currentUrl);
      $("#ocr-api-url-help").text(preset.ajuda);
      $("#ocr-model-wrap").toggleClass("hidden", !preset.usaModelo);
      OCR.refreshVisibilidade();
    },
    refreshVisibilidade: function () {
      var api = $("#ocr-mode").val() === "api";
      $("#ocr-provider-wrap").toggleClass("hidden", !api);
      $("#ocr-api-url-wrap").toggleClass("hidden", !api);
      $("#ocr-api-key-wrap").toggleClass("hidden", !api);
      $("#ocr-idioma-wrap").toggleClass("hidden", api);
      $("#ocr-psm-wrap").toggleClass("hidden", api);
    },
    salvar: function () {
      return {
        mode: $("#ocr-mode").val(),
        provider: $("#ocr-provider").val(),
        apiUrl: currentValue($("#ocr-api-url"), $("#ocr-api-url-custom")),
        apiKey: $("#ocr-api-key").val(),
        model: currentValue($("#ocr-model"), $("#ocr-model-custom")),
        idioma: $.trim($("#ocr-idioma").val()),
        psm: $.trim($("#ocr-psm").val())
      };
    }
  };

  // ---------- aba: Comparação de imagens ----------

  var ComparacaoImagens = {
    endpoint: "/api/config/image-comparison",
    carregar: function () {
      var $status = $('[data-status-for="image-comparison-config-form"]');
      $.getJSON(this.endpoint)
        .done(function (data) {
          data = data || {};
          $("#cmp-mode").val(data.mode === "api" ? "api" : "local");
          $("#cmp-provider").val(data.provider || "google-vision");
          ComparacaoImagens.refreshFields(data.model, data.apiUrl);
          if (data.apiKeyMasked) {
            $("#cmp-api-key").attr("placeholder", data.apiKeyMasked);
            $("#cmp-api-key-help").text("Uma chave já está salva. Deixe em branco para mantê-la.");
          }
          ComparacaoImagens.refreshVisibilidade();
        })
        .fail(function () {
          ComparacaoImagens.refreshFields("", "");
          setStatus($status, "Não foi possível carregar a configuração atual.", "error");
        });
    },
    refreshFields: function (currentModel, currentUrl) {
      var preset = VISAO_CMP[$("#cmp-provider").val()] || VISAO_CMP.outro;
      populateSelect($("#cmp-model"), $("#cmp-model-custom"), preset.models, currentModel);
      populateSelect($("#cmp-api-url"), $("#cmp-api-url-custom"), preset.urls, currentUrl);
      $("#cmp-api-url-help").text(preset.ajuda);
      ComparacaoImagens.refreshVisibilidade();
    },
    refreshVisibilidade: function () {
      var api = $("#cmp-mode").val() === "api";
      $("#cmp-provider-wrap").toggleClass("hidden", !api);
      $("#cmp-api-url-wrap").toggleClass("hidden", !api);
      $("#cmp-model-wrap").toggleClass("hidden", !api);
      $("#cmp-api-key-wrap").toggleClass("hidden", !api);
    },
    salvar: function () {
      return {
        mode: $("#cmp-mode").val(),
        provider: $("#cmp-provider").val(),
        apiUrl: currentValue($("#cmp-api-url"), $("#cmp-api-url-custom")),
        apiKey: $("#cmp-api-key").val(),
        model: currentValue($("#cmp-model"), $("#cmp-model-custom"))
      };
    }
  };

  // ---------- aba: Comentário IA no relatório ----------

  var ComentarioIA = {
    endpoint: "/api/config/ai/report",
    carregar: function () {
      var $status = $('[data-status-for="report-ai-config-form"]');
      $.getJSON(this.endpoint)
        .done(function (data) {
          data = data || {};
          $("#report-provider").val(data.provider || "openai");
          $("#report-anonimizar").prop("checked", !!data.anonimizar);
          $("#report-reasoning").val(data.reasoningEffort || "low");
          ComentarioIA.refreshFields(data.model, data.apiUrl);
          CERTIFICADOS.report.mostrar(data.caCertResumo);
          if (data.apiKeyMasked) {
            $("#report-api-key").attr("placeholder", data.apiKeyMasked);
            $("#report-api-key-help").text("Uma chave já está salva. Deixe em branco para mantê-la.");
          }
        })
        .fail(function () {
          ComentarioIA.refreshFields("", "");
          setStatus($status, "Não foi possível carregar a configuração atual.", "error");
        });
    },
    refreshFields: function (currentModel, currentUrl) {
      var preset = CHAT_PRESETS[$("#report-provider").val()] || CHAT_PRESETS.outro;
      populateSelect($("#report-model"), $("#report-model-custom"), preset.models, currentModel);
      populateSelect($("#report-api-url"), $("#report-api-url-custom"), preset.urls, currentUrl);
      var comCertificado = $("#report-provider").val() === "openai-cert";
      CERTIFICADOS.report.alternar(comCertificado);
      alternarRaciocinio("report", comCertificado);
    },
    salvar: function () {
      return $.extend({
        provider: $("#report-provider").val(),
        model: currentValue($("#report-model"), $("#report-model-custom")),
        apiUrl: currentValue($("#report-api-url"), $("#report-api-url-custom")),
        apiKey: $("#report-api-key").val(),
        anonimizar: $("#report-anonimizar").is(":checked"),
        reasoningEffort: $("#report-reasoning").val()
      }, CERTIFICADOS.report.payload());
    }
  };

  // ---------- aba: Análise de documentos ----------

  var AnaliseDocumentos = {
    endpoint: "/api/config/ai/document",
    carregar: function () {
      var $status = $('[data-status-for="document-ai-config-form"]');
      $.getJSON(this.endpoint)
        .done(function (data) {
          data = data || {};
          $("#doc-provider").val(data.provider || "openai");
          $("#doc-reasoning").val(data.reasoningEffort || "low");
          AnaliseDocumentos.refreshFields(data.model, data.apiUrl);
          CERTIFICADOS.doc.mostrar(data.caCertResumo);
          if (data.apiKeyMasked) {
            $("#doc-api-key").attr("placeholder", data.apiKeyMasked);
            $("#doc-api-key-help").text("Uma chave já está salva. Deixe em branco para mantê-la.");
          }
        })
        .fail(function () {
          AnaliseDocumentos.refreshFields("", "");
          setStatus($status, "Não foi possível carregar a configuração atual.", "error");
        });
    },
    refreshFields: function (currentModel, currentUrl) {
      var preset = CHAT_PRESETS[$("#doc-provider").val()] || CHAT_PRESETS.outro;
      populateSelect($("#doc-model"), $("#doc-model-custom"), preset.models, currentModel);
      populateSelect($("#doc-api-url"), $("#doc-api-url-custom"), preset.urls, currentUrl);
      var comCertificado = $("#doc-provider").val() === "openai-cert";
      CERTIFICADOS.doc.alternar(comCertificado);
      alternarRaciocinio("doc", comCertificado);
    },
    salvar: function () {
      return $.extend({
        provider: $("#doc-provider").val(),
        model: currentValue($("#doc-model"), $("#doc-model-custom")),
        apiUrl: currentValue($("#doc-api-url"), $("#doc-api-url-custom")),
        apiKey: $("#doc-api-key").val(),
        reasoningEffort: $("#doc-reasoning").val()
      }, CERTIFICADOS.doc.payload());
    }
  };

  // ---------- aba: Resumo noturno ----------

  var ResumoNoturno = {
    endpoint: "/api/config/ai/resumo-noturno",
    carregar: function () {
      var $status = $('[data-status-for="resumo-noturno-config-form"]');
      $.getJSON(this.endpoint)
        .done(function (data) {
          data = data || {};
          $("#resumo-provider").val(data.provider || "openai");
          $("#resumo-reasoning").val(data.reasoningEffort || "low");
          $("#resumo-agendador").prop("checked", data.agendador !== false);
          $("#resumo-hora").val(data.horaAgendador || "02:00");
          $("#resumo-fuso").val(data.fusoAgendador || "");
          // linha de status do agendador: quando roda de novo e como foi a última vez
          $("#resumo-agendador-estado").text(
            "Próxima execução: " + (data.agendadorProxima || "—") +
            " · Última: " + (data.agendadorUltima || "—"));
          ResumoNoturno.refreshFields(data.model, data.apiUrl);
          CERTIFICADOS.resumo.mostrar(data.caCertResumo);
          if (data.apiKeyMasked) {
            $("#resumo-api-key").attr("placeholder", data.apiKeyMasked);
            $("#resumo-api-key-help").text("Uma chave já está salva. Deixe em branco para mantê-la.");
          }
        })
        .fail(function () {
          ResumoNoturno.refreshFields("", "");
          setStatus($status, "Não foi possível carregar a configuração atual.", "error");
        });
    },
    refreshFields: function (currentModel, currentUrl) {
      var preset = CHAT_PRESETS[$("#resumo-provider").val()] || CHAT_PRESETS.outro;
      populateSelect($("#resumo-model"), $("#resumo-model-custom"), preset.models, currentModel);
      populateSelect($("#resumo-api-url"), $("#resumo-api-url-custom"), preset.urls, currentUrl);
      var comCertificado = $("#resumo-provider").val() === "openai-cert";
      CERTIFICADOS.resumo.alternar(comCertificado);
      alternarRaciocinio("resumo", comCertificado);
    },
    salvar: function () {
      return $.extend({
        provider: $("#resumo-provider").val(),
        model: currentValue($("#resumo-model"), $("#resumo-model-custom")),
        apiUrl: currentValue($("#resumo-api-url"), $("#resumo-api-url-custom")),
        apiKey: $("#resumo-api-key").val(),
        reasoningEffort: $("#resumo-reasoning").val(),
        agendador: $("#resumo-agendador").is(":checked"),
        horaAgendador: $.trim($("#resumo-hora").val()),
        fusoAgendador: $.trim($("#resumo-fuso").val())
      }, CERTIFICADOS.resumo.payload());
    }
  };

  // ---------- aba: Prompts de IA (cascata geral → unidade → delegacia → usuário) ----------
  // Não é o formulário genérico de .env da tela: cada prompt é uma linha em SEGURANCA.PROMPTS_IA
  // (rotas em configuracao_admin.py, resolução em funcoes/ia_prompt_niveis.py). A aba gerencia as
  // linhas e mostra a prévia da cascata; o botão "Salvar" de cada bloco fala direto com a API.

  var NIVEL_ROTULO = { sistema: "Geral do sistema", unidade: "Unidade superior", delegacia: "Delegacia", usuario: "Usuário" };
  var opcoesPrompts = { unidades: [], delegacias: [], usuarios: [] };

  var PromptsNiveis = {
    endpoint: "/api/config/prompts-ia",

    popularEntidades: function () {
      var nivel = $("#pn-nivel").val() || "unidade";
      var $sel = $("#pn-entidade");
      var atual = $sel.val();
      $sel.empty();
      (opcoesPrompts[nivel + "s"] || []).forEach(function (o) {
        $sel.append($("<option>").val(o.id).text(o.nome));
      });
      if (atual && $sel.find('option[value="' + atual + '"]').length) $sel.val(atual);
    },

    popularPrevia: function () {
      var nivel = $("#pn-previa-nivel").val() || "usuario";
      var $sel = $("#pn-previa-entidade");
      var atual = $sel.val();
      $sel.empty();
      (opcoesPrompts[nivel + "s"] || []).forEach(function (o) {
        $sel.append($("<option>").val(o.id).text(o.nome));
      });
      if (atual && $sel.find('option[value="' + atual + '"]').length) $sel.val(atual);
    },

    renderLista: function (linhas) {
      var $lista = $("#pn-lista").empty();
      $("#pn-vazio").prop("hidden", linhas.length > 0);
      linhas.forEach(function (p) {
        var $tr = $("<tr>");
        var $nivel = $("<td>").text(NIVEL_ROTULO[p.tp_nivel] || p.tp_nivel);
        if (!p.in_ativa) {
          $nivel.append(document.createTextNode(" "));
          $nivel.append($("<span>").addClass("badge badge-neutro").text("inativa"));
        }
        $nivel.appendTo($tr);
        $("<td>").text(p.no_entidade).appendTo($tr);
        var resumo = String(p.tx_prompt || "");
        var $texto = $("<td>").text(resumo.length > 120 ? resumo.slice(0, 120) + "…" : resumo);
        $texto.attr("title", p.tx_prompt);
        $texto.appendTo($tr);
        var $acoes = $("<td>").addClass("delegacia-acoes").appendTo($tr);
        $("<button>").attr("type", "button").addClass("btn btn-secondary btn-sm").text("Editar")
          .on("click", function () {
            $("#pn-nivel").val(p.tp_nivel);
            PromptsNiveis.popularEntidades();
            $("#pn-entidade").val(String(p.id_entidade));
            $("#pn-texto").val(p.tx_prompt);
            setStatus($("#pn-status"), "");
          }).appendTo($acoes);
        $("<button>").attr("type", "button").addClass("btn btn-danger btn-sm").text("Remover")
          .on("click", function () {
            $.ajax({ url: "/api/config/prompts-ia/" + p.id_prompt, method: "DELETE" })
              .done(PromptsNiveis.carregar)
              .fail(function (xhr) {
                Dialogo.alerta((xhr.responseJSON && xhr.responseJSON.detail) || "Não foi possível remover.");
              });
          }).appendTo($acoes);
        $lista.append($tr);
      });
    },

    carregar: function () {
      $.getJSON(PromptsNiveis.endpoint)
        .done(function (linhas) {
          var resto = [];
          (linhas || []).forEach(function (p) {
            if (p.tp_nivel === "sistema") {
              PromptsNiveis.sistema = p;
              $("#pn-sistema-texto").val(p.tx_prompt);
            } else {
              resto.push(p);
            }
          });
          PromptsNiveis.renderLista(resto);
        });
      $.getJSON(PromptsNiveis.endpoint + "/opcoes")
        .done(function (o) {
          opcoesPrompts = o || opcoesPrompts;
          PromptsNiveis.popularEntidades();
          PromptsNiveis.popularPrevia();
        });
    },

    salvar: function ($btn, $status, payload) {
      $btn.prop("disabled", true);
      $.ajax({ url: PromptsNiveis.endpoint, method: "POST", contentType: "application/json", data: JSON.stringify(payload) })
        .done(function () {
          setStatus($status, "Salvo.", "success");
          PromptsNiveis.carregar();
        })
        .fail(function (xhr) {
          setStatus($status, (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao salvar.", "error");
        })
        .always(function () { $btn.prop("disabled", false); });
    }
  };

  $("#pn-nivel").on("change", PromptsNiveis.popularEntidades);
  $("#pn-previa-nivel").on("change", PromptsNiveis.popularPrevia);
  $("#pn-sistema-salvar").on("click", function () {
    PromptsNiveis.salvar($(this), $("#pn-sistema-status"),
      { tp_nivel: "sistema", id_entidade: null, tx_prompt: $("#pn-sistema-texto").val() });
  });
  $("#pn-salvar").on("click", function () {
    var id = $("#pn-entidade").val();
    if (!id) { setStatus($("#pn-status"), "Escolha a entidade.", "error"); return; }
    PromptsNiveis.salvar($(this), $("#pn-status"),
      { tp_nivel: $("#pn-nivel").val(), id_entidade: Number(id), tx_prompt: $("#pn-texto").val() });
  });
  $("#pn-previa-btn").on("click", function () {
    var $btn = $(this), $status = $("#pn-previa-status");
    var id = $("#pn-previa-entidade").val();
    if (!id) { setStatus($status, "Escolha a entidade.", "error"); return; }
    $btn.prop("disabled", true);
    $.getJSON("/api/config/prompts-ia/efetivo", { tp_nivel: $("#pn-previa-nivel").val(), id: id })
      .done(function (d) {
        $("#pn-previa").val(d.bloco || "(sem instruções adicionais nesta cadeia)");
        setStatus($status, "");
      })
      .fail(function (xhr) {
        setStatus($status, (xhr.responseJSON && xhr.responseJSON.detail) || "Erro.", "error");
      })
      .always(function () { $btn.prop("disabled", false); });
  });

  var SECOES = {
    "prompt-ia": PromptIA,
    "prompts-ia": PromptsNiveis,
    "busca-semantica": BuscaSemantica,
    "rag-hibrido": RagHibrido,
    "ocr": OCR,
    "comparacao-imagens": ComparacaoImagens,
    "comentario-ia": ComentarioIA,
    "analise-documentos": AnaliseDocumentos,
    "resumo-noturno": ResumoNoturno
  };

  // id do form (ex.: "prompt-config-form") → chave da seção em SECOES
  var FORM_PARA_SECAO = {
    "prompt": "prompt-ia",
    "embeddings": "busca-semantica",
    "rag": "rag-hibrido",
    "ocr": "ocr",
    "image-comparison": "comparacao-imagens",
    "report-ai": "comentario-ia",
    "document-ai": "analise-documentos",
    "resumo-noturno": "resumo-noturno"
  };

  // ---------- download de modelos (Ollama) ----------

  function baixarModelo(modelo, apiUrl, $btn, $status) {
    if (!modelo) {
      Dialogo.alerta("Escolha (ou digite) o modelo antes de baixar.");
      return;
    }
    $btn.prop("disabled", true);
    $status.text("Iniciando download…");
    $.ajax({
      url: "/api/config/ollama/pull",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({ model: modelo, apiUrl: apiUrl })
    })
      .done(function () { acompanharPull(modelo, $btn, $status); })
      .fail(function (xhr) {
        if (xhr.status === 409) { // já baixando (ex.: página recarregada no meio) — só acompanha
          acompanharPull(modelo, $btn, $status);
          return;
        }
        $btn.prop("disabled", false);
        $status.text("");
        var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao iniciar o download.";
        Dialogo.alerta({ titulo: "Falha no download", mensagem: msg });
      });
  }

  function acompanharPull(modelo, $btn, $status) {
    var timer = setInterval(function () {
      $.getJSON("/api/config/ollama/pull", { model: modelo })
        .done(function (st) {
          if (st.status === "concluido") {
            clearInterval(timer);
            $btn.prop("disabled", false);
            $status.text("Modelo disponível no servidor ✓");
          } else if (st.status === "erro") {
            clearInterval(timer);
            $btn.prop("disabled", false);
            $status.text("");
            Dialogo.alerta({ titulo: "Falha no download", mensagem: st.erro || "Erro desconhecido." });
          } else {
            var detalhe = st.detalhe ? " (" + st.detalhe + ")" : "";
            $status.text("Baixando… " + (st.progresso || 0) + "%" + detalhe);
          }
        });
    }, 2000);
  }

  // ---------- eventos ----------

  // abas
  $(".extracao-tab[data-tab]").on("click", function () {
    switchTab($(this).attr("data-tab"));
  });

  // Prompt IA
  $("#prompt-provider").on("change", function () { PromptIA.refreshFields("", ""); });
  $("#prompt-model").on("change", function () { $("#prompt-model-custom").toggleClass("hidden", $(this).val() !== OUTRO_VALUE); });
  $("#prompt-api-url").on("change", function () { $("#prompt-api-url-custom").toggleClass("hidden", $(this).val() !== OUTRO_VALUE); });
  $("#prompt-pull-btn").on("click", function () {
    baixarModelo(
      currentValue($("#prompt-model"), $("#prompt-model-custom")),
      currentValue($("#prompt-api-url"), $("#prompt-api-url-custom")),
      $("#prompt-pull-btn"), $("#prompt-pull-status")
    );
  });

  // Busca semântica
  $("#emb-mode").on("change", function () {
    BuscaSemantica.refreshFields("", "");
  });
  $("#emb-provider").on("change", function () {
    BuscaSemantica.refreshFields("", "");
  });
  $("#emb-api-url").on("change", function () { $("#emb-api-url-custom").toggleClass("hidden", $(this).val() !== OUTRO_VALUE); });
  $("#emb-model").on("change", function () { $("#emb-model-custom").toggleClass("hidden", $(this).val() !== OUTRO_VALUE); });
  $("#emb-pull-btn").on("click", function () {
    // modo local usa o serviço Ollama do compose; URL vazia cai no padrão do backend
    baixarModelo(currentValue($("#emb-model"), $("#emb-model-custom")), "", $("#emb-pull-btn"), $("#emb-pull-status"));
  });

  // OCR
  $("#ocr-mode").on("change", function () { OCR.refreshVisibilidade(); });
  $("#ocr-provider").on("change", function () { OCR.refreshFields("", ""); });
  $("#ocr-api-url").on("change", function () { $("#ocr-api-url-custom").toggleClass("hidden", $(this).val() !== OUTRO_VALUE); });
  $("#ocr-model").on("change", function () { $("#ocr-model-custom").toggleClass("hidden", $(this).val() !== OUTRO_VALUE); });

  // Comparação de imagens
  $("#cmp-mode").on("change", function () { ComparacaoImagens.refreshVisibilidade(); });
  $("#cmp-provider").on("change", function () { ComparacaoImagens.refreshFields("", ""); });
  $("#cmp-api-url").on("change", function () { $("#cmp-api-url-custom").toggleClass("hidden", $(this).val() !== OUTRO_VALUE); });
  $("#cmp-model").on("change", function () { $("#cmp-model-custom").toggleClass("hidden", $(this).val() !== OUTRO_VALUE); });

  // Comentário IA no relatório
  $("#report-provider").on("change", function () { ComentarioIA.refreshFields("", ""); });
  $("#report-model").on("change", function () { $("#report-model-custom").toggleClass("hidden", $(this).val() !== OUTRO_VALUE); });
  $("#report-api-url").on("change", function () { $("#report-api-url-custom").toggleClass("hidden", $(this).val() !== OUTRO_VALUE); });

  // Análise de documentos
  $("#doc-provider").on("change", function () { AnaliseDocumentos.refreshFields("", ""); });
  $("#doc-model").on("change", function () { $("#doc-model-custom").toggleClass("hidden", $(this).val() !== OUTRO_VALUE); });
  $("#doc-api-url").on("change", function () { $("#doc-api-url-custom").toggleClass("hidden", $(this).val() !== OUTRO_VALUE); });

  // Resumo noturno
  $("#resumo-provider").on("change", function () { ResumoNoturno.refreshFields("", ""); });
  $("#resumo-model").on("change", function () { $("#resumo-model-custom").toggleClass("hidden", $(this).val() !== OUTRO_VALUE); });
  $("#resumo-api-url").on("change", function () { $("#resumo-api-url-custom").toggleClass("hidden", $(this).val() !== OUTRO_VALUE); });

  // submissão genérica: cada form aponta para a seção própria
  $("form[id$='-config-form']").on("submit", function (e) {
    e.preventDefault();
    var $form = $(this);
    var nome = FORM_PARA_SECAO[$form.attr("id").replace("-config-form", "")];
    var secao = SECOES[nome];
    if (!secao) return;

    var $saveBtn = $form.find(".save-btn");
    var $status = $form.find(".form-status");
    setStatus($status, "");
    $saveBtn.prop("disabled", true).html('<span class="spinner"></span> Salvando…');

    $.ajax({
      url: secao.endpoint,
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify(secao.salvar())
    })
      .done(function () {
        setStatus($status, "Configuração salva.", "success");
        $form.find('input[type="password"]').val("");
        secao.carregar();
      })
      .fail(function (xhr) {
        var msg = (xhr.responseJSON && xhr.responseJSON.detail) || Api.mensagemErro(xhr, "Erro ao salvar.");
        setStatus($status, msg, "error");
      })
      .always(function () {
        $saveBtn.prop("disabled", false).html(ICONE_SALVAR + " Salvar");
      });
  });

  // ---------- init ----------

  montarSelectProvedores($("#prompt-provider"), CHAT_PROVIDERS);
  montarSelectProvedores($("#report-provider"), CHAT_PROVIDERS);
  montarSelectProvedores($("#doc-provider"), CHAT_PROVIDERS);
  montarSelectProvedores($("#resumo-provider"), CHAT_PROVIDERS);
  montarSelectProvedores($("#emb-provider"), CHAT_PROVIDERS);
  montarSelectProvedores($("#ocr-provider"), [
    { value: "google-vision", label: VISAO_OCR["google-vision"].label },
    { value: "qwen-vision", label: VISAO_OCR["qwen-vision"].label },
    { value: "outro", label: VISAO_OCR.outro.label }
  ]);
  montarSelectProvedores($("#cmp-provider"), [
    { value: "google-vision", label: VISAO_CMP["google-vision"].label },
    { value: "qwen-vision", label: VISAO_CMP["qwen-vision"].label },
    { value: "outro", label: VISAO_CMP.outro.label }
  ]);

  var abaInicial = "prompt-ia";
  try {
    abaInicial = localStorage.getItem(CHAVE_ABA) || abaInicial;
  } catch (e) { /* sem persistência */ }
  if (!$(".extracao-tab[data-tab='" + abaInicial + "']").length) abaInicial = "prompt-ia";
  switchTab(abaInicial);
})();