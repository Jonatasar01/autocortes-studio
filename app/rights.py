"""Classificação preventiva de direitos — por categoria, com justificativa e estado de verificação.

Princípios:
- Baseada nas declarações do usuário (não há verificação externa).
- "Não reconhecido" nunca vira "livre". Não há consulta ao Content ID do YouTube.
- Direitos autorais, conteúdo reutilizado e monetização são questões distintas.
"""
from __future__ import annotations

SOURCE_RIGHTS = {'owned', 'licensed', 'third_party', 'unknown'}
YES_NO_UNKNOWN = {'yes', 'no', 'unknown'}
GRAPHICS = {'none', 'owned', 'licensed', 'third_party', 'unknown'}

STATUS_LABELS = {
    'declarado': 'Declarado pelo usuário (sem verificação externa)',
    'revisar': 'Revisar evidências',
    'risco_alto': 'Risco relevante',
    'nao_verificado': 'Não verificado',
    'nao_aplicavel': 'Não se aplica',
}


def _item(key: str, label: str, status: str, reason: str, action: str, verification: str) -> dict:
    return {'key': key, 'label': label, 'status': status, 'status_label': STATUS_LABELS[status],
            'reason': reason, 'recommended_action': action, 'verification': verification}


def rights_review(source_rights: str, external_audio: str, license_notes: str, has_audio: bool,
                  graphics: str = 'unknown', third_party_footage: str = 'unknown',
                  monetization: str = 'unknown') -> dict:
    notes = (license_notes or '').strip()
    declared = 'Declaração do usuário'
    items: list[dict] = []

    # 1. Vídeo original
    if source_rights == 'owned':
        items.append(_item('video_original', 'Vídeo original', 'declarado',
                           'Informado como gravado e criado pelo próprio usuário.',
                           'Guarde os arquivos originais como prova de autoria.', declared))
    elif source_rights == 'licensed':
        items.append(_item('video_original', 'Vídeo original', 'declarado' if notes else 'revisar',
                           'Licença informada com notas/comprovante.' if notes else
                           'Licença informada, mas sem comprovante ou escopo descrito.',
                           'Confirme se a licença cobre edição, plataformas, território, prazo e uso comercial.',
                           declared))
    elif source_rights == 'third_party':
        items.append(_item('video_original', 'Vídeo original', 'risco_alto',
                           'Material de terceiros: exige licença ou permissão expressa do titular.',
                           'Obtenha autorização por escrito ou use apenas material próprio/licenciado.', declared))
    else:
        items.append(_item('video_original', 'Vídeo original', 'nao_verificado',
                           'A origem do vídeo não foi confirmada.',
                           'Confirme quem é o titular antes de publicar.', 'Nenhuma'))

    # 2. Música e áudio
    if not has_audio:
        items.append(_item('musica_audio', 'Música e trilhas sonoras', 'nao_aplicavel',
                           'O vídeo não possui trilha de áudio.', '—', 'ffprobe'))
    elif external_audio == 'yes':
        items.append(_item('musica_audio', 'Música e trilhas sonoras', 'revisar' if notes else 'risco_alto',
                           'Há música/áudio de terceiros declarado.' +
                           (' Notas de licença informadas.' if notes else ' Sem comprovante de licença.'),
                           'Confirme a licença para sincronização, edição e monetização na plataforma.', declared))
    elif external_audio == 'no':
        items.append(_item('musica_audio', 'Música e trilhas sonoras', 'declarado',
                           'Declarado como áudio próprio ou autorizado.',
                           'Mantenha as licenças das trilhas usadas.', declared))
    else:
        items.append(_item('musica_audio', 'Música e trilhas sonoras', 'nao_verificado',
                           'Direitos sobre música e áudio não foram confirmados.',
                           'Identifique todas as músicas usadas e suas licenças.', declared))

    # 3. Imagens e elementos gráficos
    graphics_map = {
        'none': ('declarado', 'Declarado sem imagens, logos ou artes de terceiros.', 'Nenhuma ação adicional informada.'),
        'owned': ('declarado', 'Imagens e elementos gráficos declarados como próprios.', 'Guarde os arquivos fonte.'),
        'licensed': ('declarado' if notes else 'revisar',
                     'Imagens licenciadas (ex.: banco de imagens).' + ('' if notes else ' Sem comprovante informado.'),
                     'Confira se a licença permite uso comercial em vídeo.'),
        'third_party': ('risco_alto', 'Imagens, logos ou artes de terceiros sem licença declarada.',
                        'Obtenha permissão ou substitua os elementos.'),
        'unknown': ('nao_verificado', 'Não foi informado se há imagens, logos ou artes de terceiros.',
                    'Revise o vídeo em busca de imagens/logos de terceiros.'),
    }
    status, reason, action = graphics_map.get(graphics, graphics_map['unknown'])
    items.append(_item('imagens_graficos', 'Imagens e elementos gráficos', status, reason, action,
                       declared if graphics != 'unknown' else 'Nenhuma'))

    # 4. Material audiovisual de terceiros inserido (trechos de filmes, TV, outros vídeos)
    if third_party_footage == 'yes' or source_rights == 'third_party':
        items.append(_item('audiovisual_terceiros', 'Trechos audiovisuais de terceiros', 'risco_alto',
                           'Há trechos de filmes, TV, transmissões ou vídeos de outras pessoas.',
                           'Obtenha licença; o uso como citação/comentário depende de análise jurídica caso a caso.',
                           declared))
    elif third_party_footage == 'no':
        items.append(_item('audiovisual_terceiros', 'Trechos audiovisuais de terceiros', 'declarado',
                           'Declarado sem trechos de vídeos de terceiros.', '—', declared))
    else:
        items.append(_item('audiovisual_terceiros', 'Trechos audiovisuais de terceiros', 'nao_verificado',
                           'Não foi informado se o vídeo contém trechos de outros vídeos.',
                           'Revise o material antes de publicar.', 'Nenhuma'))

    # 5. Conteúdo reutilizado (política de plataforma, distinta de copyright)
    if source_rights == 'third_party' or third_party_footage == 'yes':
        reused = _item('conteudo_reutilizado', 'Conteúdo reutilizado (política da plataforma)', 'risco_alto',
                       'Conteúdo de terceiros sem transformação significativa pode ser considerado reutilizado, '
                       'mesmo com licença.', 'Acrescente comentário, análise ou narração própria substancial.',
                       'Política de plataforma; avaliação humana')
    elif source_rights == 'owned' and third_party_footage == 'no':
        reused = _item('conteudo_reutilizado', 'Conteúdo reutilizado (política da plataforma)', 'declarado',
                       'Material declarado como original.', 'Evite publicar cortes repetidos em vários canais.',
                       declared)
    else:
        reused = _item('conteudo_reutilizado', 'Conteúdo reutilizado (política da plataforma)', 'nao_verificado',
                       'Sem informações suficientes sobre a originalidade do material.',
                       'Confirme a origem do material e o valor original acrescentado.', 'Nenhuma')
    items.append(reused)

    # 6. Uso comercial e monetização
    worst = {i['status'] for i in items}
    if monetization == 'no':
        mon_status, mon_reason = 'revisar', 'Uso declarado como não comercial. Licenças ainda podem restringir a publicação.'
    elif 'risco_alto' in worst:
        mon_status, mon_reason = 'risco_alto', 'Há itens de risco relevante: reivindicações ou desmonetização são possíveis.'
    elif 'nao_verificado' in worst or monetization == 'unknown':
        mon_status, mon_reason = 'nao_verificado', 'Itens não verificados impedem avaliar o uso comercial.'
    else:
        mon_status, mon_reason = 'revisar', ('Sem pendências declaradas, mas a elegibilidade de monetização é decidida '
                                             'pela plataforma e nenhuma ferramenta local consegue confirmá-la.')
    items.append(_item('monetizacao', 'Uso comercial e monetização', mon_status, mon_reason,
                       'Confira os termos das licenças e as políticas do programa de parcerias da plataforma.',
                       'Não verificável localmente'))

    statuses = [i['status'] for i in items if i['key'] != 'monetizacao']
    if 'risco_alto' in statuses:
        level = 'alto'
    elif 'nao_verificado' in statuses or 'revisar' in statuses:
        level = 'revisao'
    else:
        level = 'informado_sem_alertas'
    alerts = [f"{i['label']}: {i['reason']}" for i in items if i['status'] in ('risco_alto', 'revisar', 'nao_verificado')
              and i['key'] != 'monetizacao']
    return {
        'level': level,
        'items': items,
        'alerts': alerts,
        'requires_acknowledgement': level == 'alto',
        'reused_policy': reused['reason'],
        'confidence': 'Baseado nas declarações do usuário; sem busca em base global de obras protegidas e sem '
                      'consulta ao Content ID.',
        'verification': ['Confirmar titularidade, licenças, uso comercial, território e prazo',
                         'Revisar música, imagens, logos e materiais de terceiros',
                         'No YouTube, conferir o painel "Verificações" do YouTube Studio antes de tornar o vídeo público'],
        'disclaimer': 'Análise preventiva, não liberação jurídica. Não ser reconhecido não significa estar livre de '
                      'direitos autorais, e a ausência de reivindicação não garante monetização.',
    }
