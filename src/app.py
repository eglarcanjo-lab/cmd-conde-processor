import os
import io
import re
import zipfile
import traceback
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
from processor import processar_clientes, processar_pedidos, processar_inadimplencia, processar_tasks, processar_produtos_base, processar_faturamento_mktp, processar_pontos_bees, calcular_rv_completa, processar_visitacao_gv, processar_rota_coaching, processar_dto_gc, processar_aba_promocao, calcular_politica_comercial, calcular_execucao_menu, calcular_tarefas_cerveja, processar_score5, calcular_tarefas_nab, calcular_tarefas_volume, calcular_tarefas_marketplace, calcular_tarefas_match, calcular_tarefas_cerveja_zero, calcular_todos_spo_tasks, processar_pedido_alone, processar_rgb, processar_cupons_digitais, processar_loja_ideal, processar_scanntech, processar_portfolio_ideal, processar_atendimento_produtivo, processar_devolucoes_relatorio, processar_grade_estoque, processar_faturados, processar_buffer
from sheets_service import ler_aba, sobrescrever_aba, atualizar_status_arquivo
import pandas as pd

load_dotenv()

app = Flask(__name__)
CORS(app, origins=[
    os.environ.get("FRONTEND_URL", "http://localhost:5173"),
    "https://*.vercel.app",
])

# Token obrigatório via env (sem default fixo no código — D1/D2 da Auditoria 2).
# Se não estiver configurado, rejeita tudo com log claro (não derruba o serviço).
PROCESSOR_TOKEN = os.environ.get("PROCESSOR_TOKEN")
if not PROCESSOR_TOKEN:
    print("⚠️ CRÍTICO: PROCESSOR_TOKEN não configurado — todas as requisições "
          "serão rejeitadas (401). Defina a env var PROCESSOR_TOKEN no Render.")


def verificar_token(req):
    if not PROCESSOR_TOKEN:
        return False
    token = req.headers.get("X-Processor-Token") or req.args.get("token")
    return token == PROCESSOR_TOKEN


def _eh_quota_429(e):
    """True se a exceção for quota 429 do Google Sheets (gspread)."""
    try:
        import gspread
        if isinstance(e, gspread.exceptions.APIError):
            return getattr(getattr(e, "response", None), "status_code", None) == 429
    except Exception:
        pass
    return "429" in str(e) and "quota" in str(e).lower()


@app.errorhandler(Exception)
def _handle_erro(e):
    """Qualquer erro não tratado: quota do Sheets vira mensagem clara (429);
    o resto vira 500 com a mensagem encurtada — nunca HTML cru pro Node."""
    # Erros HTTP normais (404, 405, etc.) passam direto — não viram 500.
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e
    traceback.print_exc()
    if _eh_quota_429(e):
        return jsonify({
            "error": "Quota do Google Sheets excedida. Aguarde ~1 minuto e "
                     "importe menos relatórios por vez."
        }), 429
    return jsonify({"error": str(e)[:200] or "Erro interno no processador."}), 500


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "cmd-conde-processor"})


@app.route("/api/migrar/sheets-para-sql", methods=["POST"])
def migrar_sheets_sql():
    """Carga inicial Sheets → Postgres (Supabase). Protegido por token. Idempotente:
    pode rodar de novo (recria/recarrega). Requer a env DATABASE_URL no processador."""
    if not verificar_token(request):
        return jsonify({"error": "Token inválido."}), 401
    try:
        from etl import migrar_tudo  # import tardio: só carrega psycopg2 quando chamado
        relatorio = migrar_tudo()
        ok = sum(1 for v in relatorio.values() if str(v).startswith("✅"))
        return jsonify({"success": True, "abas_ok": ok, "relatorio": relatorio})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)[:300]}), 500


@app.route("/api/processar/clientes", methods=["POST"])
def upload_clientes():
    """Recebe o arquivo 0105070402 e processa."""
    if not verificar_token(request):
        return jsonify({"error": "Token inválido."}), 401

    if "arquivo" not in request.files:
        return jsonify({"error": "Envie o arquivo no campo 'arquivo'."}), 400

    arquivo = request.files["arquivo"]
    conteudo = arquivo.read()

    try:
        df_clientes = processar_clientes(conteudo)
        return jsonify({
            "success": True,
            "message": f"Clientes processados: {len(df_clientes)} PDVs.",
        })
    except Exception as e:
        traceback.print_exc()
        atualizar_status_arquivo("0105070402 (Clientes)", "❌ ERRO", str(e)[:200])
        return jsonify({"error": str(e)}), 500


@app.route("/api/processar/pedidos", methods=["POST"])
def upload_pedidos():
    """Recebe o arquivo 03014701 e processa."""
    if not verificar_token(request):
        return jsonify({"error": "Token inválido."}), 401

    if "arquivo" not in request.files:
        return jsonify({"error": "Envie o arquivo no campo 'arquivo'."}), 400

    arquivo = request.files["arquivo"]
    conteudo = arquivo.read()

    try:
        # Tenta carregar base de clientes já processada
        df_clientes = ler_aba("pdv_base")
        processar_pedidos(conteudo, df_clientes if not df_clientes.empty else None)
        # Recalcula RV automaticamente após atualizar volumes
        try:
            calcular_rv_completa()
        except Exception as e_rv:
            print(f"  ⚠️ Auto-recalc RV após pedidos: {e_rv}")
        return jsonify({
            "success": True,
            "message": "Pedidos processados e RV recalculada.",
        })
    except Exception as e:
        traceback.print_exc()
        atualizar_status_arquivo("03014701 (Pedidos)", "❌ ERRO", str(e)[:200])
        return jsonify({"error": str(e)}), 500


def _rodar_spo(arquivos, campo, fn, resultados, mes_ref=None, usa_mes=True, msg="processado"):
    """Importa um relatório SPO uniforme: lê o arquivo, chama o processador e
    registra ✅/❌ em resultados. Para incluir um KPI novo, basta uma linha no
    dispatch chamando este helper (ver upload_ambos)."""
    if campo not in arquivos:
        return
    try:
        if usa_mes:
            fn(arquivos[campo].read(), mes_ref=mes_ref)
        else:
            fn(arquivos[campo].read())
        resultados[campo] = f"✅ {msg}"
    except Exception as e:
        traceback.print_exc()
        resultados[campo] = f"❌ Erro: {str(e)[:100]}"


@app.route("/api/processar/ambos", methods=["POST"])
def upload_ambos():
    """Recebe clientes + pedidos e processa tudo de uma vez."""
    if not verificar_token(request):
        return jsonify({"error": "Token inválido."}), 401

    arquivos = request.files
    # Mês de referência explícito — enviado pela UI (ex: "2026-05")
    # Garante que relatórios sem coluna de data sejam atribuídos ao mês correto
    _mes_ref = request.form.get("mes_ref") or None
    if _mes_ref:
        print(f"📅 Mês de referência recebido da UI: {_mes_ref}")
    resultados = {}

    # Clientes primeiro (pedidos dependem da base de PDVs)
    if "clientes" in arquivos:
        try:
            df_clientes = processar_clientes(arquivos["clientes"].read())
            resultados["clientes"] = f"✅ {len(df_clientes)} PDVs processados"
        except Exception as e:
            traceback.print_exc()
            resultados["clientes"] = f"❌ Erro: {str(e)[:100]}"
            df_clientes = None
    else:
        # Lê a base de PDVs já existente (pedidos dependem dela). Não deve
        # derrubar o import quando o arquivo de clientes não foi enviado.
        try:
            df_clientes = ler_aba("pdv_base")
        except Exception as e:
            print(f"⚠️ Não foi possível ler pdv_base: {e}")
            df_clientes = None
        # Não mostra no resultado quando não enviado

    if "faturamento_mktp" in arquivos:
        try:
            processar_faturamento_mktp(arquivos["faturamento_mktp"].read())
            resultados["faturamento_mktp"] = "✅ Processado com sucesso"
        except Exception as e:
            traceback.print_exc()
            resultados["faturamento_mktp"] = f"❌ Erro: {str(e)[:100]}"

    # SPO uniformes (ver helper _rodar_spo) — incluir KPI novo = 1 linha aqui.
    _rodar_spo(arquivos, "spo_promo",        processar_aba_promocao, resultados, _mes_ref, msg="Aba Promoção processada")
    _rodar_spo(arquivos, "spo_dto",          processar_dto_gc,       resultados, _mes_ref, msg="DTO GC processado")
    _rodar_spo(arquivos, "spo_coaching",     processar_rota_coaching, resultados, _mes_ref, usa_mes=False, msg="Rota Coaching processada")
    _rodar_spo(arquivos, "spo_visitacao_gv", processar_visitacao_gv, resultados, _mes_ref, msg="Visitação GV processada")

    if "pontos_bees" in arquivos:
        try:
            processar_pontos_bees(arquivos["pontos_bees"].read())
            resultados["pontos_bees"] = "✅ Processados com sucesso"
        except Exception as e:
            traceback.print_exc()
            resultados["pontos_bees"] = f"❌ Erro: {str(e)[:100]}"

    if "produtos_base" in arquivos:
        try:
            processar_produtos_base(arquivos["produtos_base"].read())
            resultados["produtos_base"] = "✅ Base de produtos atualizada"
        except Exception as e:
            traceback.print_exc()
            resultados["produtos_base"] = f"❌ Erro: {str(e)[:100]}"

    if "tasks" in arquivos:
        try:
            processar_tasks(arquivos["tasks"].read())
            resultados["tasks"] = "✅ Processadas com sucesso"
            # Recalcula SPO automaticamente (leitura única do Sheets)
            try:
                calcular_todos_spo_tasks()
            except Exception as e:
                print(f"Erro SPO tasks: {e}")
        except Exception as e:
            traceback.print_exc()
            resultados["tasks"] = f"❌ Erro: {str(e)[:100]}"

    _rodar_spo(arquivos, "spo_score5",          processar_score5,                resultados, _mes_ref, msg="Score 5 processado")
    _rodar_spo(arquivos, "spo_cupons",          processar_cupons_digitais,       resultados, _mes_ref, msg="Cupons Digitais processado")
    _rodar_spo(arquivos, "spo_loja_ideal",      processar_loja_ideal,            resultados, _mes_ref, msg="Loja Ideal processado")
    _rodar_spo(arquivos, "spo_scanntech",       processar_scanntech,             resultados, _mes_ref, msg="Scanntech processado")
    _rodar_spo(arquivos, "spo_portfolio_ideal", processar_portfolio_ideal,       resultados, _mes_ref, msg="Portfólio Ideal processado")
    _rodar_spo(arquivos, "spo_ap",              processar_atendimento_produtivo, resultados, _mes_ref, msg="Atendimento Produtivo processado")
    _rodar_spo(arquivos, "spo_rgb",             processar_rgb,                   resultados, _mes_ref, msg="+RGB processado")
    _rodar_spo(arquivos, "spo_alone",           processar_pedido_alone,          resultados, _mes_ref, msg="Pedido Alone processado")

    if "inadimplencia" in arquivos:
        try:
            processar_inadimplencia(arquivos["inadimplencia"].read())
            resultados["inadimplencia"] = "✅ Processada com sucesso"
        except Exception as e:
            traceback.print_exc()
            resultados["inadimplencia"] = f"❌ Erro: {str(e)[:100]}"

    if "devolucoes" in arquivos:
        try:
            df_dev = processar_devolucoes_relatorio(arquivos["devolucoes"].read(), mes_ref=_mes_ref)
            resultados["devolucoes"] = f"✅ {len(df_dev)} devoluções processadas"
        except Exception as e:
            traceback.print_exc()
            resultados["devolucoes"] = f"❌ Erro: {str(e)[:100]}"

    if "grade" in arquivos:
        try:
            df_g = processar_grade_estoque(arquivos["grade"].read())
            resultados["grade"] = f"✅ {len(df_g)} itens em estoque"
        except Exception as e:
            traceback.print_exc()
            resultados["grade"] = f"❌ Erro: {str(e)[:100]}"

    if "pedidos" in arquivos:
        try:
            processar_pedidos(
                arquivos["pedidos"].read(),
                df_clientes if df_clientes is not None and not df_clientes.empty else None
            )
            resultados["pedidos"] = "✅ Processados com sucesso"
        except Exception as e:
            traceback.print_exc()
            resultados["pedidos"] = f"❌ Erro: {str(e)[:100]}"

    # Faturados/Buffer rodam DEPOIS de pedidos (dependem de pedido_chave).
    if "faturados" in arquivos:
        try:
            df_f = processar_faturados(arquivos["faturados"].read())
            resultados["faturados"] = f"✅ {len(df_f)} linhas (Faturados)"
        except Exception as e:
            traceback.print_exc()
            resultados["faturados"] = f"❌ Erro: {str(e)[:100]}"

    if "buffer" in arquivos:
        try:
            df_b = processar_buffer(arquivos["buffer"].read())
            resultados["buffer"] = f"✅ {len(df_b)} linhas (Buffer)"
        except Exception as e:
            traceback.print_exc()
            resultados["buffer"] = f"❌ Erro: {str(e)[:100]}"

    # Libera a memória das fases pesadas do import (Render free = 512MB).
    import gc
    df_clientes = None
    gc.collect()

    # RV NÃO é recalculada aqui de propósito: somada ao import (carrega cobertura ~20k)
    # estourava os 512MB e derrubava o worker (OOM não é capturável por except).
    # Recalcule num passo separado, com a memória livre: POST /api/rv/calcular.
    rv_keys = {"pedidos", "faturamento_mktp", "pontos_bees", "spo_ap"}
    if rv_keys & set(arquivos.keys()):
        resultados["rv_recalculada"] = "⏳ Recalcule a RV à parte: POST /api/rv/calcular"

    return jsonify({"success": True, "resultados": resultados})


@app.route("/api/status-arquivos")
def status_arquivos():
    """Retorna o status de atualização de cada arquivo."""
    if not verificar_token(request):
        return jsonify({"error": "Token inválido."}), 401
    df = ler_aba("status_arquivos")
    return jsonify(df.to_dict(orient="records") if not df.empty else [])


@app.route("/api/produtos-sem-categoria")
def produtos_sem_categoria():
    """Lista produtos sem categoria cadastrada."""
    if not verificar_token(request):
        return jsonify({"error": "Token inválido."}), 401
    df = ler_aba("produtos_sem_categoria")
    return jsonify(df.to_dict(orient="records") if not df.empty else [])



@app.route("/api/processar/inadimplencia", methods=["POST"])
def upload_inadimplencia():
    """Recebe o arquivo 121601 e processa."""
    if not verificar_token(request):
        return jsonify({"error": "Token inválido."}), 401
    if "arquivo" not in request.files:
        return jsonify({"error": "Envie o arquivo no campo 'arquivo'."}), 400
    try:
        df = processar_inadimplencia(request.files["arquivo"].read())
        return jsonify({"success": True, "message": f"Inadimplência processada: {len(df)} PDVs."})
    except Exception as e:
        traceback.print_exc()
        atualizar_status_arquivo("121601 (Inadimplência)", "❌ ERRO", str(e)[:200])
        return jsonify({"error": str(e)}), 500


@app.route("/api/processar/devolucoes", methods=["POST"])
def upload_devolucoes():
    """Recebe o relatório de devoluções (entregas frustradas) e processa."""
    if not verificar_token(request):
        return jsonify({"error": "Token inválido."}), 401
    if "arquivo" not in request.files:
        return jsonify({"error": "Envie o arquivo no campo 'arquivo'."}), 400
    try:
        mes_ref = request.form.get("mes_ref") or None
        df = processar_devolucoes_relatorio(request.files["arquivo"].read(), mes_ref=mes_ref)
        return jsonify({"success": True, "message": f"Devoluções processadas: {len(df)} linhas."})
    except Exception as e:
        traceback.print_exc()
        atualizar_status_arquivo("Devoluções (Entregas Frustradas)", "❌ ERRO", str(e)[:200])
        return jsonify({"error": str(e)}), 500


@app.route("/api/processar/grade", methods=["POST"])
def upload_grade():
    """Grade de estoque (saldo Disp por produto)."""
    if not verificar_token(request):
        return jsonify({"error": "Token inválido."}), 401
    if "arquivo" not in request.files:
        return jsonify({"error": "Envie o arquivo no campo 'arquivo'."}), 400
    try:
        df = processar_grade_estoque(request.files["arquivo"].read())
        return jsonify({"success": True, "message": f"Grade de estoque: {len(df)} itens."})
    except Exception as e:
        traceback.print_exc()
        atualizar_status_arquivo("Grade de Estoque", "❌ ERRO", str(e)[:200])
        return jsonify({"error": str(e)}), 500


@app.route("/api/processar/faturados", methods=["POST"])
def upload_faturados():
    """Rotina 030237 — clientes com NF faturada. Cruza com pedido_chave."""
    if not verificar_token(request):
        return jsonify({"error": "Token inválido."}), 401
    if "arquivo" not in request.files:
        return jsonify({"error": "Envie o arquivo no campo 'arquivo'."}), 400
    try:
        df = processar_faturados(request.files["arquivo"].read())
        return jsonify({"success": True, "message": f"Faturados processados: {len(df)} linhas."})
    except Exception as e:
        traceback.print_exc()
        atualizar_status_arquivo("030237 (Faturados)", "❌ ERRO", str(e)[:200])
        return jsonify({"error": str(e)}), 500


@app.route("/api/processar/buffer", methods=["POST"])
def upload_buffer():
    """Rotina 030111 — pedidos parados no Buffer. Cruza com pedido_chave."""
    if not verificar_token(request):
        return jsonify({"error": "Token inválido."}), 401
    if "arquivo" not in request.files:
        return jsonify({"error": "Envie o arquivo no campo 'arquivo'."}), 400
    try:
        df = processar_buffer(request.files["arquivo"].read())
        return jsonify({"success": True, "message": f"Buffer processado: {len(df)} linhas."})
    except Exception as e:
        traceback.print_exc()
        atualizar_status_arquivo("030111 (Buffer)", "❌ ERRO", str(e)[:200])
        return jsonify({"error": str(e)}), 500


@app.route("/api/processar/tasks", methods=["POST"])
def upload_tasks():
    """Recebe o arquivo de tasks (xlsx) e processa."""
    if not verificar_token(request):
        return jsonify({"error": "Token inválido."}), 401
    if "arquivo" not in request.files:
        return jsonify({"error": "Envie o arquivo no campo 'arquivo'."}), 400
    try:
        df = processar_tasks(request.files["arquivo"].read())
        return jsonify({"success": True, "message": f"Tasks processadas: {len(df)}."})
    except Exception as e:
        traceback.print_exc()
        atualizar_status_arquivo("Tasks (BI)", "❌ ERRO", str(e)[:200])
        return jsonify({"error": str(e)}), 500


@app.route("/api/processar/produtos-base", methods=["POST"])
def upload_produtos_base():
    """Recebe o arquivo 0111 (base de produtos) e processa."""
    if not verificar_token(request):
        return jsonify({"error": "Token inválido."}), 401
    if "arquivo" not in request.files:
        return jsonify({"error": "Envie o arquivo no campo 'arquivo'."}), 400
    try:
        df = processar_produtos_base(request.files["arquivo"].read())
        return jsonify({"success": True, "message": f"Base de produtos: {len(df)} produtos."})
    except Exception as e:
        traceback.print_exc()
        atualizar_status_arquivo("0111 (Produtos)", "❌ ERRO", str(e)[:200])
        return jsonify({"error": str(e)}), 500


@app.route("/api/processar/faturamento-mktp", methods=["POST"])
def upload_faturamento_mktp():
    if not verificar_token(request): return jsonify({"error": "Token inválido."}), 401
    if "arquivo" not in request.files: return jsonify({"error": "Envie o arquivo."}), 400
    try:
        df = processar_faturamento_mktp(request.files["arquivo"].read())
        # Recalcula RV automaticamente após atualizar faturamento Mktp
        try:
            calcular_rv_completa()
        except Exception as e_rv:
            print(f"  ⚠️ Auto-recalc RV após faturamento_mktp: {e_rv}")
        return jsonify({"success": True, "message": f"Faturamento Mktp: {len(df)} setores. RV recalculada."})
    except Exception as e:
        traceback.print_exc()
        atualizar_status_arquivo("030509 (Faturamento Mktp)", "❌ ERRO", str(e)[:200])
        return jsonify({"error": str(e)}), 500


@app.route("/api/processar/pontos-bees", methods=["POST"])
def upload_pontos_bees():
    if not verificar_token(request): return jsonify({"error": "Token inválido."}), 401
    if "arquivo" not in request.files: return jsonify({"error": "Envie o arquivo."}), 400
    try:
        df = processar_pontos_bees(request.files["arquivo"].read())
        # Recalcula RV automaticamente após atualizar pontos
        try:
            calcular_rv_completa()
        except Exception as e_rv:
            print(f"  ⚠️ Auto-recalc RV após pontos_bees: {e_rv}")
        return jsonify({"success": True, "message": f"Pontos Bees: {len(df)} setores. RV recalculada."})
    except Exception as e:
        traceback.print_exc()
        atualizar_status_arquivo("Pontos Bees (BI)", "❌ ERRO", str(e)[:200])
        return jsonify({"error": str(e)}), 500


@app.route("/api/rv/calcular", methods=["POST"])
def calcular_rv():
    if not verificar_token(request): return jsonify({"error": "Token inválido."}), 401
    try:
        df = calcular_rv_completa()
        return jsonify({"success": True, "message": f"RV calculada: {len(df)} setores."})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/processar/spo-visitacao-gv", methods=["POST"])
def upload_spo_visitacao_gv():
    if not verificar_token(request): return jsonify({"error": "Token inválido."}), 401
    if "arquivo" not in request.files: return jsonify({"error": "Envie o arquivo."}), 400
    try:
        df = processar_visitacao_gv(request.files["arquivo"].read())
        return jsonify({"success": True, "message": f"Visitação GV: {len(df)} linhas."})
    except Exception as e:
        traceback.print_exc()
        atualizar_status_arquivo("SPO - Visitação GV", "❌ ERRO", str(e)[:200])
        return jsonify({"error": str(e)}), 500


@app.route("/api/processar/spo-coaching", methods=["POST"])
def upload_spo_coaching():
    if not verificar_token(request): return jsonify({"error": "Token inválido."}), 401
    if "arquivo" not in request.files: return jsonify({"error": "Envie o arquivo."}), 400
    try:
        df = processar_rota_coaching(request.files["arquivo"].read())
        return jsonify({"success": True, "message": f"Rota Coaching: {len(df)} registros."})
    except Exception as e:
        traceback.print_exc()
        atualizar_status_arquivo("SPO - Rota Coaching", "❌ ERRO", str(e)[:200])
        return jsonify({"error": str(e)}), 500


@app.route("/api/processar/spo-dto", methods=["POST"])
def upload_spo_dto():
    if not verificar_token(request): return jsonify({"error": "Token inválido."}), 401
    if "arquivo" not in request.files: return jsonify({"error": "Envie o arquivo."}), 400
    try:
        df = processar_dto_gc(request.files["arquivo"].read())
        return jsonify({"success": True, "message": "DTO GC processado."})
    except Exception as e:
        traceback.print_exc()
        atualizar_status_arquivo("SPO - DTO GC", "❌ ERRO", str(e)[:200])
        return jsonify({"error": str(e)}), 500


@app.route("/api/processar/spo-promo", methods=["POST"])
def upload_spo_promo():
    if not verificar_token(request): return jsonify({"error": "Token inválido."}), 401
    if "arquivo" not in request.files: return jsonify({"error": "Envie o arquivo."}), 400
    try:
        df = processar_aba_promocao(request.files["arquivo"].read())
        return jsonify({"success": True, "message": f"Aba Promoção: {len(df)} PDVs."})
    except Exception as e:
        traceback.print_exc()
        atualizar_status_arquivo("SPO - Aba Promoção", "❌ ERRO", str(e)[:200])
        return jsonify({"error": str(e)}), 500


@app.route("/api/calcular/spo-politica", methods=["POST"])
def calcular_spo_politica():
    if not verificar_token(request): return jsonify({"error": "Token inválido."}), 401
    try:
        df = calcular_politica_comercial()
        return jsonify({"success": True, "message": f"Política Comercial calculada: {len(df)} setores."})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500



@app.route("/api/calcular/spo-tasks-cerveja", methods=["POST"])
def calcular_spo_tasks_cerveja():
    if not verificar_token(request): return jsonify({"error": "Token inválido."}), 401
    try:
        df = calcular_tarefas_cerveja()
        return jsonify({"success": True, "message": f"Tasks Cerveja calculadas: {len(df)} setores."})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500



@app.route("/api/processar/spo-score5", methods=["POST"])
def upload_spo_score5():
    if not verificar_token(request): return jsonify({"error": "Token inválido."}), 401
    if "arquivo" not in request.files: return jsonify({"error": "Envie o arquivo."}), 400
    try:
        df = processar_score5(request.files["arquivo"].read())
        return jsonify({"success": True, "message": f"Score 5: {len(df)} setores."})
    except Exception as e:
        traceback.print_exc()
        atualizar_status_arquivo("SPO - Score 5 (ON_TRADE)", "❌ ERRO", str(e)[:200])
        return jsonify({"error": str(e)}), 500



@app.route("/api/calcular/spo-tasks-volume", methods=["POST"])
def calcular_spo_tasks_volume():
    if not verificar_token(request): return jsonify({"error": "Token inválido."}), 401
    try:
        df = calcular_tarefas_volume()
        return jsonify({"success": True, "message": f"Tasks Volume calculadas: {len(df)} setores."})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500



@app.route("/api/calcular/spo-tasks-marketplace", methods=["POST"])
def calcular_spo_tasks_marketplace():
    if not verificar_token(request): return jsonify({"error": "Token inválido."}), 401
    try:
        df = calcular_tarefas_marketplace()
        return jsonify({"success": True, "message": f"Tasks Marketplace calculadas: {len(df)} setores."})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500



@app.route("/api/calcular/spo-tasks-match", methods=["POST"])
def calcular_spo_tasks_match():
    if not verificar_token(request): return jsonify({"error": "Token inválido."}), 401
    try:
        df = calcular_tarefas_match()
        return jsonify({"success": True, "message": f"Tasks MATCH calculadas: {len(df)} setores."})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500



@app.route("/api/calcular/spo-tasks-cerv-zero", methods=["POST"])
def calcular_spo_tasks_cerv_zero():
    if not verificar_token(request): return jsonify({"error": "Token inválido."}), 401
    try:
        df = calcular_tarefas_cerveja_zero()
        return jsonify({"success": True, "message": f"Tasks Cerveja Zero calculadas: {len(df)} setores."})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500



@app.route("/api/calcular/spo-tasks-digitalizacao", methods=["POST"])
def calcular_spo_tasks_digitalizacao():
    if not verificar_token(request): return jsonify({"error": "Token inválido."}), 401
    try:
        from processor import _calcular_tasks_com_df
        from sheets_service import ler_aba
        df_tasks = ler_aba("tasks")
        _calcular_tasks_com_df(df_tasks, "digitalização bees", None, None,
                               "spo_tasks_digit_resumo", "SPO - Tasks Digitalização", 60)
        return jsonify({"success": True, "message": "Tasks Digitalização calculadas."})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500



@app.route("/api/processar/spo-alone", methods=["POST"])
def upload_spo_alone():
    if not verificar_token(request): return jsonify({"error": "Token inválido."}), 401
    if "arquivo" not in request.files: return jsonify({"error": "Envie o arquivo."}), 400
    try:
        df = processar_pedido_alone(request.files["arquivo"].read())
        return jsonify({"success": True, "message": f"Pedido Alone: {len(df)} setores."})
    except Exception as e:
        traceback.print_exc()
        atualizar_status_arquivo("SPO - Pedido Alone", "❌ ERRO", str(e)[:200])
        return jsonify({"error": str(e)}), 500



@app.route("/api/processar/spo-rgb", methods=["POST"])
def upload_spo_rgb():
    if not verificar_token(request): return jsonify({"error": "Token inválido."}), 401
    if "arquivo" not in request.files: return jsonify({"error": "Envie o arquivo."}), 400
    try:
        df = processar_rgb(request.files["arquivo"].read())
        return jsonify({"success": True, "message": f"+RGB: {len(df)} PDVs processados."})
    except Exception as e:
        traceback.print_exc()
        atualizar_status_arquivo("SPO - +RGB Total", "❌ ERRO", str(e)[:200])
        return jsonify({"error": str(e)}), 500



@app.route("/api/processar/spo-cupons", methods=["POST"])
def upload_spo_cupons():
    if not verificar_token(request): return jsonify({"error": "Token inválido."}), 401
    if "arquivo" not in request.files: return jsonify({"error": "Envie o arquivo."}), 400
    try:
        df = processar_cupons_digitais(request.files["arquivo"].read())
        return jsonify({"success": True, "message": f"Cupons: {len(df)} setores."})
    except Exception as e:
        traceback.print_exc()
        atualizar_status_arquivo("SPO - Cupons Digitais", "❌ ERRO", str(e)[:200])
        return jsonify({"error": str(e)}), 500



@app.route("/api/processar/spo-loja-ideal", methods=["POST"])
def upload_spo_loja_ideal():
    if not verificar_token(request): return jsonify({"error": "Token inválido."}), 401
    if "arquivo" not in request.files: return jsonify({"error": "Envie o arquivo."}), 400
    try:
        df = processar_loja_ideal(request.files["arquivo"].read())
        return jsonify({"success": True, "message": f"Loja Ideal: {len(df)} setores."})
    except Exception as e:
        traceback.print_exc()
        atualizar_status_arquivo("SPO - Loja Ideal Vizinhança", "❌ ERRO", str(e)[:200])
        return jsonify({"error": str(e)}), 500



@app.route("/api/processar/spo-scanntech", methods=["POST"])
def upload_spo_scanntech():
    if not verificar_token(request): return jsonify({"error": "Token inválido."}), 401
    if "arquivo" not in request.files: return jsonify({"error": "Envie o arquivo."}), 400
    try:
        df = processar_scanntech(request.files["arquivo"].read())
        return jsonify({"success": True, "message": f"Scanntech: {len(df)} linhas."})
    except Exception as e:
        traceback.print_exc()
        atualizar_status_arquivo("SPO - Expansão Scanntech", "❌ ERRO", str(e)[:200])
        return jsonify({"error": str(e)}), 500



@app.route("/api/processar/spo-portfolio-ideal", methods=["POST"])
def upload_spo_portfolio_ideal():
    if not verificar_token(request): return jsonify({"error": "Token inválido."}), 401
    if "arquivo" not in request.files: return jsonify({"error": "Envie o arquivo."}), 400
    try:
        df = processar_portfolio_ideal(request.files["arquivo"].read())
        return jsonify({"success": True, "message": f"Portfólio Ideal: {len(df)} setores."})
    except Exception as e:
        traceback.print_exc()
        atualizar_status_arquivo("SPO - Portfólio Ideal Score 5", "❌ ERRO", str(e)[:200])
        return jsonify({"error": str(e)}), 500



@app.route("/api/processar/spo-ap", methods=["POST"])
def upload_spo_ap():
    if not verificar_token(request): return jsonify({"error": "Token inválido."}), 401
    if "arquivo" not in request.files: return jsonify({"error": "Envie o arquivo."}), 400
    try:
        df = processar_atendimento_produtivo(request.files["arquivo"].read())
        # Recalcula RV automaticamente (AP é gate de pagamento)
        try:
            calcular_rv_completa()
        except Exception as e_rv:
            print(f"  ⚠️ Auto-recalc RV após spo_ap: {e_rv}")
        return jsonify({"success": True, "message": f"Atendimento Produtivo: {len(df)} linhas. RV recalculada."})
    except Exception as e:
        traceback.print_exc()
        atualizar_status_arquivo("SPO - Atendimento Produtivo", "❌ ERRO", str(e)[:200])
        return jsonify({"error": str(e)}), 500


# ─── AUTO-IMPORT POR ZIP (e-mail/Apps Script) ───────────────────────────────
# Recebe 1 .zip com vários relatórios e detecta o tipo de cada um PELO NOME:
#  • Promax: número da rotina no nome (03014701, 0111, ...)
#  • BI: "#<item>" (#1, #5, #7, ...) · "task" → Tasks · "pontos"/"bees" → Pontos Bees
ROTINAS = {
    "0105070402": "clientes", "03014701": "pedidos", "0111": "produtos_base",
    "030509": "faturamento_mktp", "120601": "inadimplencia", "030204": "devolucoes",
    "020304": "grade",
}
ITENS_BI = {
    "1": "spo_visitacao_gv", "2": "spo_coaching", "5": "spo_ap", "6": "spo_dto",
    "7": "spo_promo", "12": "spo_score5", "19": "spo_alone", "20": "spo_rgb",
    "21": "spo_cupons", "22": "spo_loja_ideal", "23": "spo_scanntech", "24": "spo_portfolio_ideal",
}
ORDEM = ["clientes", "produtos_base", "faturamento_mktp", "pontos_bees",
         "spo_visitacao_gv", "spo_coaching", "spo_dto", "spo_promo", "spo_score5",
         "spo_alone", "spo_rgb", "spo_cupons", "spo_loja_ideal", "spo_scanntech",
         "spo_portfolio_ideal", "spo_ap", "tasks", "inadimplencia", "devolucoes",
         "pedidos", "grade"]


def detectar_tipo(nome):
    base = str(nome).rsplit("/", 1)[-1]
    low = base.lower()
    if "task" in low:
        return "tasks"
    if "ponto" in low or "bees" in low:
        return "pontos_bees"
    m = re.search(r"#\s*(\d+)", base)
    if m and m.group(1) in ITENS_BI:
        return ITENS_BI[m.group(1)]
    # Promax: dígitos do começo do nome (antes de _ ou espaço), ignorando pontos
    prefixo = re.split(r"[ _]", base)[0]
    digs = re.sub(r"\D", "", prefixo)
    if digs:
        if digs in ROTINAS:
            return ROTINAS[digs]
        for rot in sorted(ROTINAS, key=len, reverse=True):
            if digs.startswith(rot):
                return ROTINAS[rot]
    return None


@app.route("/api/processar/zip", methods=["POST"])
def upload_zip():
    if not verificar_token(request):
        return jsonify({"error": "Token inválido."}), 401
    if "arquivo" not in request.files:
        return jsonify({"error": "Envie o zip no campo 'arquivo'."}), 400
    mes_ref = request.form.get("mes_ref") or None
    try:
        zf = zipfile.ZipFile(io.BytesIO(request.files["arquivo"].read()))
    except Exception as e:
        return jsonify({"error": f"Arquivo não é um .zip válido: {e}"}), 400

    por_campo, ignorados = {}, []
    for info in zf.infolist():
        if info.is_dir():
            continue
        nome = info.filename.rsplit("/", 1)[-1]
        if not nome or nome.startswith(".") or nome.startswith("__MACOSX"):
            continue
        tipo = detectar_tipo(info.filename)
        if tipo:
            por_campo[tipo] = zf.read(info)  # se repetir o tipo, o último vence
        else:
            ignorados.append(nome)

    resultados = {}
    df_clientes = None
    precisa_rv = False
    for campo in ORDEM:
        if campo not in por_campo:
            continue
        b = por_campo[campo]
        try:
            if campo == "clientes":
                df_clientes = processar_clientes(b)
            elif campo == "produtos_base":
                processar_produtos_base(b)
            elif campo == "faturamento_mktp":
                processar_faturamento_mktp(b); precisa_rv = True
            elif campo == "pontos_bees":
                processar_pontos_bees(b); precisa_rv = True
            elif campo == "spo_visitacao_gv":
                processar_visitacao_gv(b, mes_ref=mes_ref)
            elif campo == "spo_coaching":
                processar_rota_coaching(b)
            elif campo == "spo_dto":
                processar_dto_gc(b, mes_ref=mes_ref)
            elif campo == "spo_promo":
                processar_aba_promocao(b, mes_ref=mes_ref)
            elif campo == "spo_score5":
                processar_score5(b, mes_ref=mes_ref)
            elif campo == "spo_alone":
                processar_pedido_alone(b, mes_ref=mes_ref)
            elif campo == "spo_rgb":
                processar_rgb(b, mes_ref=mes_ref)
            elif campo == "spo_cupons":
                processar_cupons_digitais(b, mes_ref=mes_ref)
            elif campo == "spo_loja_ideal":
                processar_loja_ideal(b, mes_ref=mes_ref)
            elif campo == "spo_scanntech":
                processar_scanntech(b, mes_ref=mes_ref)
            elif campo == "spo_portfolio_ideal":
                processar_portfolio_ideal(b, mes_ref=mes_ref)
            elif campo == "spo_ap":
                processar_atendimento_produtivo(b, mes_ref=mes_ref); precisa_rv = True
            elif campo == "tasks":
                processar_tasks(b)
                try:
                    calcular_todos_spo_tasks()
                except Exception as e:
                    print(f"  ⚠️ SPO tasks: {e}")
            elif campo == "inadimplencia":
                processar_inadimplencia(b)
            elif campo == "devolucoes":
                processar_devolucoes_relatorio(b, mes_ref=mes_ref)
            elif campo == "pedidos":
                dfc = df_clientes
                if dfc is None or dfc.empty:
                    dfc = ler_aba("pdv_base")
                processar_pedidos(b, dfc if dfc is not None and not dfc.empty else None)
                precisa_rv = True
            elif campo == "grade":
                processar_grade_estoque(b)
            resultados[campo] = "✅ OK"
        except Exception as e:
            traceback.print_exc()
            resultados[campo] = f"❌ Erro: {str(e)[:120]}"

    if precisa_rv:
        try:
            calcular_rv_completa()
            resultados["rv_recalculada"] = "✅ OK"
        except Exception as e:
            resultados["rv_recalculada"] = f"⚠️ {str(e)[:100]}"

    return jsonify({"success": True, "processados": resultados, "ignorados": ignorados})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
