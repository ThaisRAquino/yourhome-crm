# -*- coding: utf-8 -*-
"""
Modo demonstração: cria um banco SEPARADO (demo_crm.db) com usuários e protocolos
fictícios e sobe o sistema. Não toca no banco real (mba_crm.db).

    python demo.py            -> recria os dados de exemplo e abre em http://localhost:5000
    python demo.py --so-dados -> só recria o banco de exemplo
"""
import os
import sys
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
DEMO_DB = os.path.join(BASE, "demo_crm.db")
os.environ["MBA_DB"] = DEMO_DB
if os.path.exists(DEMO_DB):
    os.remove(DEMO_DB)

import app as M  # noqa: E402  (precisa vir depois de definir MBA_DB)
from werkzeug.security import generate_password_hash  # noqa: E402

# CPFs fictícios (válidos só no formato) e senhas da demonstração
USUARIOS = [
    ("Diretora Demo", "diretor", "11144477735", "Diretor2026"),
    ("Gustavo Gerente", "gerente", "52998224725", "Gerente2026"),
    ("Paula Supervisora", "supervisor", "16899535009", "Superv2026"),
    ("Ana Souza", "analista", "39053344705", "Analista2026"),
    ("Bruno Lima", "analista", "71428793860", "Analista2026"),
    ("Carla Dias", "analista", "87748248800", "Analista2026"),
]
CSRF = "demo" * 8


def criar_dados():
    con = M.sqlite3.connect(DEMO_DB)
    for nome, perfil, cpf, senha in USUARIOS:
        con.execute("INSERT INTO usuarios(cpf,nome,perfil,senha_hash,trocar_senha,criado_em) VALUES(?,?,?,?,0,?)",
                    (cpf, nome, perfil, generate_password_hash(senha), M.agora()))
    con.commit()
    ids = {n: i for i, n in con.execute("SELECT id, nome FROM usuarios")}
    M.app.testing = True

    def entrar(nome):
        _, _, cpf, senha = next(u for u in USUARIOS if u[0] == nome)
        c = M.app.test_client()
        with c.session_transaction() as s:
            s["_csrf"] = CSRF
        c.post("/login", data={"cpf": cpf, "senha": senha, "_csrf": CSRF})
        with c.session_transaction() as s:
            s["_csrf"] = CSRF
        return c

    sup = entrar("Paula Supervisora")
    analistas = {n: entrar(n) for n in ("Ana Souza", "Bruno Lima", "Carla Dias")}
    agora = datetime.now()

    def distribuir(prot, minutos_atras, tipo, analista, cliente, emp):
        sup.post("/distribuir", data={
            "protocolo": prot, "tipo": tipo, "analista_id": ids[analista], "cliente": cliente,
            "empreendimento": emp, "_csrf": CSRF,
            "solicitado_em": (agora - timedelta(minutes=minutos_atras)).strftime("%Y-%m-%dT%H:%M")})
        return con.execute("SELECT id FROM analises WHERE protocolo=? ORDER BY id DESC", (prot,)).fetchone()[0]

    base = {"emissao": agora.strftime("%Y-%m-%d"), "c_cpf": [""], "c_nasc": ["1990-05-10"],
            "fin_prazo": "420", "fin_amortizacao": "PRICE", "fin_enquadramento": "MCMV", "fin_taxa": "7,66% a.a.",
            "fin_meses36": "Sim", "fin_dependente": "Não", "faixa": "HIS2", "comp_serasa": "Nada consta",
            "comp_cadmut": "Nada consta", "comp_ciweb": "Off", "comp_subsidio": "Nada consta", "_csrf": CSRF}
    clientes = ["Marcos Pereira", "Juliana Rocha", "Roberto Alves", "Fernanda Costa", "Lucas Martins",
                "Patrícia Nunes", "Diego Ramos", "Camila Freitas", "Rafael Teixeira", "Larissa Gomes",
                "Thiago Barros", "Beatriz Lopes"]
    construtoras = [("Construtora Horizonte", "Residencial Jardim das Flores"),
                    ("Viva Bem Incorporadora", "Condomínio Parque Azul"),
                    ("Construtora Horizonte", "Residencial Vila Nova")]
    pareceres = ["Aprovado", "Aprovado - Condicional", "Reprovado", "Aprovado", "Reprovado", "Aprovado - Condicional"]
    motivos = iter(M.MOTIVOS_REPROVA * 3)
    nomes = list(analistas)

    # protocolos já finalizados (alimentam painel e relatórios)
    for i, cliente in enumerate(clientes):
        quem = nomes[i % 3]
        cons, emp = construtoras[i % 3]
        tipo = "MESA" if i % 4 == 0 else "LEAD"
        prazo = 30 if tipo == "MESA" else 60
        aid = distribuir(f"AP-{24100 + i}", prazo - 5 + (i % 3) * 20, tipo, quem, cliente, emp)
        analistas[quem].post(f"/analises/{aid}/iniciar", data={"_csrf": CSRF})
        st = pareceres[i % len(pareceres)]
        formal, informal = ("3.850,00", "0") if i % 2 else ("1.200,00", "2.300,00")
        d = dict(base, status=st, acao="finalizar", c_nome=[cliente], c_formal=[formal], c_informal=[informal],
                 imovel_construtora=cons, imovel_empreendimento=emp, imovel_avaliacao="265.000,00",
                 fin_financiamento=f"{198 + i * 3}.500,00", fin_fgts="12.000,00", fin_parcela="1.684,20",
                 fin_vencimento=(agora + timedelta(days=90)).strftime("%Y-%m-%d"))
        if st == "Reprovado":
            d["motivo_reprova"] = next(motivos)
        analistas[quem].post(f"/analises/{aid}/editar", data=d)

    # fila atual: em andamento, prestes a atrasar, MESA aguardando, atrasado
    a = distribuir("AP-24201", 20, "LEAD", "Ana Souza", "Eduardo Matos", "Residencial Jardim das Flores")
    analistas["Ana Souza"].post(f"/analises/{a}/iniciar", data={"_csrf": CSRF})
    distribuir("AP-24202", 22, "MESA", "Ana Souza", "Sílvia Moraes", "Condomínio Parque Azul")
    distribuir("AP-24203", 8, "LEAD", "Ana Souza", "Henrique Dias", "Residencial Vila Nova")
    distribuir("AP-24204", 70, "LEAD", "Bruno Lima", "Vanessa Prado", "Residencial Vila Nova")
    b = distribuir("AP-24205", 12, "MESA", "Carla Dias", "Otávio Reis", "Condomínio Parque Azul")
    analistas["Carla Dias"].post(f"/analises/{b}/iniciar", data={"_csrf": CSRF})
    con.close()


if __name__ == "__main__":
    criar_dados()
    print("\n  Dados de demonstração criados em demo_crm.db\n")
    print("  Logins (CPF / senha):")
    for nome, perfil, cpf, senha in USUARIOS:
        print(f"    {M.PERFIS[perfil]:<10} {nome:<20} {M.fmt_cpf(cpf)}  /  {senha}")
    if "--so-dados" not in sys.argv:
        porta = int(os.environ.get("PORT", 5000))
        print(f"\n  Abrindo em http://localhost:{porta}  (Ctrl+C para sair)\n")
        try:
            from waitress import serve
            serve(M.app, host="127.0.0.1", port=porta)
        except ImportError:
            M.app.run(host="127.0.0.1", port=porta)
