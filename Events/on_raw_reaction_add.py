import json
import emoji

from discord.ext import commands


class OnRawReactionAdd(commands.Cog):
    def __init__(self, client):
        self.client = client

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        if payload.message_id == 1211723532799709216:
            emoji_text = emoji.demojize(payload.emoji.name)
            with open('te_onboarding.json', 'r', encoding='utf-8') as f:
                onboarding_data = json.load(f)
                f.close()
            if emoji_text in onboarding_data.keys():
                if payload.member.id in onboarding_data[emoji_text]["onboard"]:
                    return
                ch = self.client.get_channel(onboarding_data[emoji_text]["channel"])
                await ch.send(onboarding_data[emoji_text]["message"].replace("[user]", f"<@{payload.member.id}>"))
                onboarding_data[emoji_text]["onboard"].append(payload.member.id)
                with open('te_onboarding.json', 'w') as f:
                    json.dump(onboarding_data, f, indent=4)
                    f.close()


    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(OnRawReactionAdd(client))
