import os
import traceback
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
from processor import processar_clientes, processar_pedidos, processar_inadimplencia, processar_tasks, processar_produtos_base, processar_faturamento_mktp, processar_pontos_bees, calcular_rv_completa, processar_visitacao_gv, processar_rota_coaching, processar_dto_gc
from sheets_service import ler_aba, sobrescrever_aba, atualizar_status_arquivo
import pandas as pd

load_dotenv()

app = Flask(__name__)
CORS(app, origins=[
    os.environ.get("FRONTEND_URL", "http://localhost:5173"),
    "https://*.vercel.app",
])

PROCESSOR_TOKEN = os.environ.get("PROCESSOR_TOKEN", "cmd_processor_secret")


def verificar_token(req):
    token = req.headers.get("X-Processor-Token") or req.args.get("token")
    return token == PROCESSOR_TOKEN


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "cmd-conde-processor"})


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
        return jsonify({
            "success": True,
            "message": "Pedidos processados com sucesso.",
        })
    except Exception as e:
        traceback.print_exc()
        atualizar_status_arquivo("03014701 (Pedidos)", "❌ ERRO", str(e)[:200])
        return jsonify({"error": str(e)}), 500


@app.route("/api/processar/ambos", methods=["POST"])
def upload_ambos():
    """Recebe clientes + pedidos e processa tudo de uma vez."""
    if not verificar_token(request):
        return jsonify({"error": "Token inválido."}), 401

    arquivos = request.files
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
        df_clientes = ler_aba("pdv_base")
        # Não mostra no resultado quando não enviado

    if "faturamento_mktp" in arquivos:
        try:
            processar_faturamento_mktp(arquivos["faturamento_mktp"].read())
            resultados["faturamento_mktp"] = "✅ Processado com sucesso"
        except Exception as e:
            traceback.print_exc()
            resultados["faturamento_mktp"] = f"❌ Erro: {str(e)[:100]}"

    if "spo_dto" in arquivos:
        try:
            processar_dto_gc(arquivos["spo_dto"].read())
            resultados["spo_dto"] = "✅ DTO GC processado"
        except Exception as e:
            traceback.print_exc()
            resultados["spo_dto"] = f"❌ Erro: {str(e)[:100]}"

    if "spo_coaching" in arquivos:
        try:
            processar_rota_coaching(arquivos["spo_coaching"].read())
            resultados["spo_coaching"] = "✅ Rota Coaching processada"
        except Exception as e:
            traceback.print_exc()
            resultados["spo_coaching"] = f"❌ Erro: {str(e)[:100]}"

    if "spo_visitacao_gv" in arquivos:
        try:
            processar_visitacao_gv(arquivos["spo_visitacao_gv"].read())
            resultados["spo_visitacao_gv"] = "✅ Visitação GV processada"
        except Exception as e:
            traceback.print_exc()
            resultados["spo_visitacao_gv"] = f"❌ Erro: {str(e)[:100]}"

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
        except Exception as e:
            traceback.print_exc()
            resultados["tasks"] = f"❌ Erro: {str(e)[:100]}"

    if "inadimplencia" in arquivos:
        try:
            processar_inadimplencia(arquivos["inadimplencia"].read())
            resultados["inadimplencia"] = "✅ Processada com sucesso"
        except Exception as e:
            traceback.print_exc()
            resultados["inadimplencia"] = f"❌ Erro: {str(e)[:100]}"

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
        return jsonify({"success": True, "message": f"Faturamento Mktp: {len(df)} setores."})
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
        return jsonify({"success": True, "message": f"Pontos Bees: {len(df)} setores."})
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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
