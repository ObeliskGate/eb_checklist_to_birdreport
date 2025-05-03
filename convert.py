from collections import Counter
from datetime import datetime, timedelta
from time import sleep
from dateutil import parser
import json
import os

import pandas as pd
from ebird.api import get_checklist, get_location

eb_species_code_to_taxa = json.load(
    open("./db/eb_species_code_to_taxa.json", "r", encoding="utf-8"))

birdreport_chn_name_to_taxa = json.load(
    open("./db/birdreport_chn_name_to_taxa.json", "r", encoding="utf-8"))


extra_name_conversion = {  # to be continued
    "金斑鸻": "金鸻", 
    "北鹰鸮": "日本鹰鸮",
    "黑额凤鹛": "黑颏凤鹛",
    "白冠燕尾": "白额燕尾",
}

def eb_name_to_z4_name(eb_name: str) -> str | None:
    if eb_name in extra_name_conversion:
        return extra_name_conversion[eb_name]
    if eb_name in birdreport_chn_name_to_taxa:
        return birdreport_chn_name_to_taxa[eb_name]["中文名"]
    return None

name_for_specification = {  # to be continued
    "橙腹叶鹎",  # 西南_
    "金腰燕",  # 斑腰燕

}


def species_code_to_z4_name(species_code: str) -> tuple[str, str | None]:
    # use English common name at first

    eb_com_name = eb_species_code_to_taxa[species_code]["comName"]
    if "(" in eb_com_name:
        eb_com_name = eb_com_name.split("(")[0].strip()
    if "未识别的" in eb_com_name or eb_com_name in name_for_specification:
        return eb_com_name, None
    
    # '/' is not processed due to names like `大嘴乌鸦/丛林鸦`

    return eb_com_name, eb_name_to_z4_name(eb_com_name)


class BirdReportObsInfo:
    def __init__(self,
        chinese_name: str,
        count: int,
        note: str  # actually `note` is useless
    ):
        self.chinese_name = chinese_name
        self.count = count
        self.note = note

    def __add__(self, other):
        if not isinstance(other, BirdReportObsInfo) or self.chinese_name != other.chinese_name:
            return NotImplemented
        return BirdReportObsInfo(
            self.chinese_name,
            self.count + other.count,
            self.note + "\n" + other.note
        )
    
    def __iter__(self):
        return iter((self.chinese_name, self.count, self.note))



def obs_to_z4(obs: pd.DataFrame) -> tuple[list[BirdReportObsInfo], list[BirdReportObsInfo]]:
    convertible_list = []
    inconvertible_list = []
    for _, item in obs.iterrows():

        note = item["comments"] if not pd.isna(item.get("comments")) else ""
        count_str = item["howManyStr"]
        species_code = item["speciesCode"]

        if count_str == "X":
            count = 0
            note += "\n数量不详"
        else:
            count = int(count_str)

        eb_name, z4_name = species_code_to_z4_name(species_code)

        if z4_name:
            convertible_list.append(
                BirdReportObsInfo(z4_name, count, note)
            )
        else:
            inconvertible_list.append(
                BirdReportObsInfo(eb_name, count, note)
            )

    return convertible_list, inconvertible_list


def merge_subspecies(lst: list[BirdReportObsInfo]) -> list[BirdReportObsInfo]:
    name_list = [it.chinese_name for it in lst]
    counts = Counter(name_list)
    duplicate_names = set(it for it in counts if counts[it] > 1)
    if not duplicate_names:
        return lst
    merged_dict = {}

    for it in lst:
        if it.chinese_name not in duplicate_names:
            merged_dict[it.chinese_name] = it
            continue

        if it.chinese_name in merged_dict:
            merged_dict[it.chinese_name] += it
        else:
            merged_dict[it.chinese_name] = it

    return list(merged_dict.values())
        



def obs_list_to_xls(lst: list[BirdReportObsInfo], filename: str):
    df = pd.DataFrame(lst, columns=["中文名", "数量", "备注"])  
    df.to_excel(filename, index=False)


class BirdReportInfo:
    def __init__(self, 
        location_name: str, 
        location_place: tuple[float, float],
        start_date: datetime, 
        end_date: datetime,
        effective_time: timedelta, 
        note: str,
        obs_list: list[BirdReportObsInfo],
    ):
        self.location_name = location_name
        self.location_place = location_place
        self.start_date = start_date
        self.end_date = end_date
        self.effective_time = effective_time
        self.note = note
        self.obs_list = obs_list


def checklist_to_birdreport_info(checklist_id: str) -> BirdReportInfo:
    token = os.getenv("EBIRD_TOKEN")
    checklist: dict = get_checklist(token, checklist_id)

    loc_id = checklist["locId"]
    loc_info = get_location(token, loc_id)
    loc_name = loc_info["locName"]
    loc_place = (loc_info["lat"], loc_info["lng"])

    start_date = parser.parse(checklist["obsDt"])
    hours = checklist.get("durationHrs", 0)
    duration = timedelta(hours=hours)
    end_date = start_date + duration

    note = checklist.get("comments", "")
    note = f"converted from eBird checklist {checklist['subId']}\n{note}"

    convertible_list, inconvertible_list = obs_to_z4(pd.DataFrame(checklist["obs"]))

    if inconvertible_list:
        print("dealing with inconvertible list:")
        for item in inconvertible_list:
            while True:
                new_name = input(
                    f"eBird name: {item.chinese_name}, convert to (n for skip, _ for repeat the name): ")
                match new_name:
                    case "n" | 'N':
                        break
                    case _:
                        new_name = new_name.replace("_", item.chinese_name)
                        try_chn_name = eb_name_to_z4_name(new_name)
                        if try_chn_name:
                            convertible_list.append(
                                BirdReportObsInfo(try_chn_name, item.count, item.note)
                            )
                            break
                        else:
                            print(f"Cannot find {new_name} in Z4, try again")
                            continue

    return BirdReportInfo(
        loc_name,
        loc_place,
        start_date,
        end_date,
        duration,
        note,
        merge_subspecies(convertible_list)
    )


if __name__ == "__main__":
    import pyperclip as pc

    id_ = input("Enter the eBird checklist ID: ")
    birdreport_info = checklist_to_birdreport_info(id_)
    print("\n\n")

    start_str = birdreport_info.start_date.strftime("%Y-%m-%d %H:%M")
    end_str = birdreport_info.end_date.strftime("%Y-%m-%d %H:%M")

    print(f"Location:   {birdreport_info.location_name} at {birdreport_info.location_place}")
    print(f"Start date: {start_str}")
    print(f"End date:   {end_str}")
    print(f"Duration:   {birdreport_info.effective_time}")
    print(f"Note:\n{birdreport_info.note}")

    pc.copy(start_str)
    sleep(1)
    pc.copy(end_str)
    sleep(1)
    pc.copy(birdreport_info.note)

    obs_list_to_xls(birdreport_info.obs_list, f"{id_}.xlsx")
