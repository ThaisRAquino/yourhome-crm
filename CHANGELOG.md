# Histórico de versões

## 1.3: nova identidade
- Nome do sistema: **Your Home**.
- Cor principal trocada para **azul-marinho** (#1E3A5F), mantendo verde, magenta, roxo e amarelo.
- Modo demonstração (`demo.py`) e testes de ponta a ponta (`tests/test_fluxos.py`) incluídos no repositório.

## 1.2: tempo de análise, reprovas e gestão
- Botões **Iniciar** e **Finalizar**: tempo de análise separado do SLA, com os dois cronômetros ao vivo.
- **Um processo por vez** por analista (exceto MESA).
- **Motivo obrigatório** na reprova e painel de **reprovados por motivo** com filtro nos relatórios da diretoria.
- **Redistribuição** de protocolo já cadastrado, puxando os dados do histórico.
- **Exclusão de usuários**, com transferência das análises e confirmação por CPF.
- Painel do gerente: volume aprovado do **mês corrente** (zera todo dia 1º), análises em andamento e tempo médio de análise.
- Relatórios da diretoria: reprovados, processos com renda formal e informal, e gráficos de aprovados e reprovados por mês.
- O gerente pode editar os dados cadastrais do diretor.
- **Reabrir** análise finalizada (gestores).

## 1.1: distribuição e SLA
- Tela **Distribuir protocolo** (nº do Anapro, horário da solicitação, LEAD/MESA e analista).
- **Fila** com contagem regressiva e cores: azul (no prazo), amarelo (prestes a atrasar) e vermelho (atrasado).
- Alerta de **MESA**, aviso sonoro e notificação de novo protocolo.
- Transferência entre analistas com histórico.
- SLA no painel da equipe e nos relatórios.
- `iniciar.bat` com diagnóstico dos problemas mais comuns no Windows.

## 1.0: primeira versão
- Login com CPF, senha padrão com troca obrigatória e quatro perfis (Analista, Supervisor, Gerente e Diretor).
- Cadastro de análises com relatório para impressão e PDF.
- Painel da equipe e relatórios gerais da diretoria.
- Exportação CSV e backup do banco.
