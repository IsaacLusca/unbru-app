import useAppSelector from "@modules/common/hooks/useAppSelector";

const MENU_URL =
    "https://raw.githubusercontent.com/IsaacLusca/unbru-app/main/data/menus.json";

const parseMenu = (jsonMenu: DayMenu[] | null) => {
    const result = {};
    jsonMenu?.map((dayMenu) => {
        Object.assign(result, {
            [dayMenu.date]: {
                desjejum: dayMenu.desjejum,
                almoco: dayMenu.almoco,
                jantar: dayMenu.jantar,
            },
        });
    });
    return result;
};

type MenusByCampus = Record<string, DayMenu[]>;

const useFetchMenu = () => {
    const selectedCampus = useAppSelector((state) => state.campus);

    async function fetchMenu() {
        try {
            const response = await fetch(`${MENU_URL}?t=${Date.now()}`);
            if (!response.ok) {
                throw new Error(`Falha ao buscar cardápio: ${response.status}`);
            }
            const data = (await response.json()) as MenusByCampus;
            return parseMenu(data[selectedCampus] ?? null);
        } catch (error) {
            console.error("[menu] falha ao buscar cardápio", error);
            return {};
        }
    }
    return fetchMenu;
};

export default useFetchMenu;
