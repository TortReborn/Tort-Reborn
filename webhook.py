import json
import os
import re
from io import BytesIO

import flask
import requests
from dotenv import load_dotenv
from waitress import serve
from flask import Flask, request, send_file
from flask_cors import CORS, cross_origin

from Helpers.classes import PlayerStats
from Helpers.database import DB
from Helpers.functions import pretty_date, generate_rank_badge, generate_banner, urlify, getPlayerUUID
from Helpers.variables import discord_ranks

from datetime import datetime

app = Flask(__name__)
cors = CORS(app)
app.config['CORS_HEADERS'] = 'Content-Type'

load_dotenv()


@app.route('/application', methods=['POST'])
def webhook():
    if request.method == 'POST':
        json_data = request.json
        image_data = None
        player_name = json_data["data"]["fields"][1]["value"]
        player_name = re.sub('[^a-zA-Z0-9_]', '', player_name)
        player = getPlayerUUID(player_name)
        ticket = json_data["data"]["fields"][0]["value"]
        db = DB()
        db.connect()

        db.cursor.execute(f'SELECT * FROM new_app WHERE channel = \'{ticket}\'')
        result = db.cursor.fetchone()
        db.close()
        data = {'username': 'Tort', 'embeds': [],
                'avatar_url': 'https://cdn.discordapp.com/app-icons/893589914434809876/3ed3452bda4e071aa5db8127f0c21006.png?size=256'}
        if result:
            url = result[3]
        if not player:
            embed = {'fields': [], 'title': ':no_entry: Oops! Something did not go as intended.',
                     'description': f'Your application was received, however information for `{player_name}` could not be obtained.\nPlease fill out the application again and make sure your minecraft username is spelled correctly.',
                     'color': int(0xe33232)}
            data['embeds'].append(embed)
            requests.post(url, json=data, headers={"Content-Type": "application/json"})
            return 'Data received with error'
        embed = {'fields': [], 'title': 'Application submitted', 'color': int(0x42f54b)}
        for field in json_data['data']['fields']:
            fld = {}
            if field['label'] == 'ticket':
                ticket = field['value']
                continue
            elif field['label'] == 'Minecraft Username':
                fld['name'] = field['label']
                fld['value'] = player[0]
            elif field['label'] == 'Timezone':
                fld['name'] = field['label']
                for timezone in field['options']:
                    if timezone['id'] == field['value']:
                        fld['value'] = timezone['text']
                        break
                embed['fields'].append(fld)
                fld = {'name': 'Link to stats page',
                       'value': f'https://wynncraft.com/stats/player/{player[1]}'}
                embed['fields'].append(fld)
                continue
            elif field['label'] is None:
                if field['value'] is not None:
                    image_data = field['value'][:]
                continue
            elif field['value'] is None:
                continue
            fld['name'] = field['label']
            fld['value'] = field['value']
            embed['fields'].append(fld)

        data['embeds'].append(embed)

        if image_data:
            for image in image_data:
                data['embeds'].append({'url': image['url'], 'image': {'url': image['url']}, 'color': int(0x42f54b)})

        db = DB()
        db.connect()

        db.cursor.execute(f'SELECT * FROM new_app WHERE channel = \'{ticket}\'')
        result = db.cursor.fetchone()
        db.close()
        if result:
            url = result[3]
            requests.post(url, json=data, headers={"Content-Type": "application/json"})
        return "Data received"


@app.route('/playerdata/<path:name>', methods=['GET'])
def playerdata(name):
    if request.method == 'GET':
        try:
            player = PlayerStats(name, 7)
        except:
            print("error")
            return

        playerdata = {}
        playerdata['name'] = player.username;
        playerdata['uuid'] = player.UUID
        playerdata['guild'] = player.guild
        playerdata['linked'] = player.linked
        playerdata['taq'] = player.taq
        if player.linked and player.taq:
            color = discord_ranks[player.rank]["color"]
            if player.rank != '✫✪✫ Hydra - Leader':
                rank = player.rank
            else:
                rank = 'Hydra'
            playerdata['shells'] = player.shells
        else:
            color = '66ccff'
            rank = player.guild_rank
            playerdata['shells'] = 0
        playerdata['rank'] = rank
        playerdata['color'] = color.replace('#', '')
        playerdata['online'] = player.online
        playerdata['background'] = player.background
        playerdata['shiny'] = player.shiny
        playerdata['server'] = player.server
        playerdata['last_seen'] = pretty_date(player.last_joined)
        playerdata['tag'] = player.tag
        playerdata['tag_color'] = player.tag_color
        playerdata['playtime'] = player.playtime
        playerdata['total_level'] = player.total_level
        if player.guild is not None:
            playerdata['in_guild_for_days'] = player.in_guild_for.days
            playerdata['guild_contribution'] = '{:,}'.format(player.guild_contributed)
            playerdata['real_xp'] = '{:,}'.format(player.real_xp)
        else:
            playerdata['in_guild_for_days'] = None
            playerdata['guild_contribution'] = None
            playerdata['real_xp'] = None

        playerdata['real_pt'] = player.real_pt

        response = flask.jsonify(playerdata)
        response.headers.add('Access-Control-Allow-Origin', '*')

        return response


@app.route('/rank_badge/<path:color>/<path:rank>', methods=['GET'])
def rank_badge(color, rank):
    badge = generate_rank_badge(rank, color, 1)
    file = BytesIO()
    badge.save(file, format="PNG")
    file.seek(0)

    return send_file(file, mimetype='image/png')


@app.route('/guild_members/<path:guild>', methods=['GET'])
def guild_members(guild):
    members = []
    url = f"https://api.wynncraft.com/v3/guild/{urlify(guild)}"

    resp = requests.get(url, timeout=10, headers={"Authorization": f"Bearer {os.getenv("WYNN_TOKEN")}"})
    resp.raise_for_status()
    data = resp.json()

    for rank in data['members']:
        if rank != 'total':
            for member in data['members'][rank]:
                member_data = data['members'][rank][member]
                member_data['name'] = member
                members.append(member_data)

    return {'members': members, 'xp': data['xpPercent'], 'level': data['level']}


@app.route('/guild_banner/<path:guild>', methods=['GET'], defaults={'style': ''})
@app.route('/guild_banner/<path:guild>/<path:style>', methods=['GET'])
def guild_banner(guild, style=''):
    badge = generate_banner(guild, 5, style)
    file = BytesIO()
    badge.save(file, format="PNG")
    file.seek(0)

    return send_file(file, mimetype='image/png')


@app.route('/xp_data/<path:uuid>', methods=['GET'])
@cross_origin()
def xp_data(uuid):
    with open('player_activity.json', 'r') as f:
        data = json.load(f)
        f.close()

    xp_contribution = []
    days = []

    for i in range(30):
        for player in data[i]['members']:
            if player['uuid'] == uuid:
                found = False
                for p in data[i + 1]['members']:
                    if p['uuid'] == uuid:
                        found = True
                        break
                if found:
                    xp_contribution.insert(0, player['contributed'] - p['contributed'])
                else:
                    xp_contribution.insert(0, player['contributed'] - 0)
                days.insert(0, datetime.utcfromtimestamp(data[i]['time'] - 3600).strftime('%d/%m/%Y'))

    return {"xp_contribution": xp_contribution, "days": days}


@app.route('/download_welcome_messages', methods=['GET'])
def download_welcome_messages():
    return send_file('welcome_messages.txt', as_attachment=True)


serve(app, host='0.0.0.0', port=8001)
# app.run(host='0.0.0.0', port=8001)
