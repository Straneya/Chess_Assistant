"""
"""

import argparse
import time
import re
import chess
import chess.engine
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import NoSuchElementException, WebDriverException

PIECE_LETTER = {
    "p": "p", "n": "n", "b": "b", "r": "r", "q": "q", "k": "k",
}


def attach_to_chrome(debug_port=9222):
    """Attach Selenium to an already-open Chrome window in debug mode."""
    options = Options()
    options.add_experimental_option("debuggerAddress", f"127.0.0.1:{debug_port}")
    driver = webdriver.Chrome(options=options)
    return driver


def find_chess_board_element(driver):
    """Locate the chess.com board container in the current page."""
    return driver.find_element("id", "board-single") if _has_id(driver, "board-single") \
        else driver.find_element("css selector", "wc-chess-board, .board")


def _has_id(driver, element_id):
    try:
        driver.find_element("id", element_id)
        return True
    except NoSuchElementException:
        return False


def read_board_pieces(driver):
    """
    Parses chess.com's DOM for piece elements.
    Each piece element has classes like: "piece wp square-58"
      - color+type: wp/wn/wb/wr/wq/wk or bp/bn/bb/br/bq/bk
      - square-XY: X = file (1-8, a-h), Y = rank (1-8)
    Returns a dict: {(file, rank): (color, piece_type)}
    """
    pieces = {}
    piece_elements = driver.find_elements("css selector", "[class*='piece']")

    for el in piece_elements:
        classes = el.get_attribute("class") or ""
        color_type_match = re.search(r"\b([wb])([pnbrqk])\b", classes)
        square_match = re.search(r"square-(\d)(\d)", classes)
        if not color_type_match or not square_match:
            continue
        color, ptype = color_type_match.groups()
        file_num, rank_num = int(square_match.group(1)), int(square_match.group(2))
        pieces[(file_num, rank_num)] = (color, ptype)

    return pieces


def board_is_flipped(driver, board_el):
    classes = board_el.get_attribute("class") or ""
    return "flipped" in classes


def pieces_to_fen(pieces, flipped, turn_guess="w"):
    """
    Convert the parsed piece dict into a FEN string.
    Castling rights / en passant are set permissively (best-effort) since
    they're hard to infer reliably from static piece positions alone.
    """
    board = chess.Board(None)  # empty board
    for (file_num, rank_num), (color, ptype) in pieces.items():
        # chess.com file/rank are already 1-8 matching a-h / 1-8 when NOT flipped
        f = file_num - 1
        r = rank_num - 1
        if flipped:
            f = 7 - f
            r = 7 - r
        square = chess.square(f, r)
        piece = chess.Piece(chess.PIECE_SYMBOLS.index(ptype), color == "w")
        board.set_piece_at(square, piece)

    board.turn = chess.WHITE if turn_guess == "w" else chess.BLACK
    # Best-effort: allow all castling if kings/rooks are on home squares
    board.clean_castling_rights()
    return board.fen()


def guess_turn_from_move_list(driver):
    """
    Counts move notation entries on the page to guess whose turn it is.
    chess.com lists each ply as an element with class containing 'node'.
    Falls back to 'w' if it can't tell.
    """
    try:
        move_nodes = driver.find_elements("css selector", "[class*='move-list'] [class*='node']")
        ply_count = len([m for m in move_nodes if m.text.strip()])
        return "w" if ply_count % 2 == 0 else "b"
    except Exception:
        return "w"


def get_best_move(engine, fen, think_time=1.0):
    board = chess.Board(fen)
    result = engine.play(board, chess.engine.Limit(time=think_time))
    info = engine.analyse(board, chess.engine.Limit(time=think_time))
    score = info["score"].white()
    return board.san(result.move), score, board.turn


def main():
    parser = argparse.ArgumentParser(description="Live chess.com move tip assistant")
    parser.add_argument("--stockfish-path", required=True, help="Path to the stockfish executable")
    parser.add_argument("--debug-port", type=int, default=9222)
    parser.add_argument("--think-time", type=float, default=1.0, help="Seconds Stockfish thinks per position")
    parser.add_argument("--poll-interval", type=float, default=2.0, help="Seconds between board reads")
    args = parser.parse_args()

    print("Attaching to Chrome...")
    driver = attach_to_chrome(args.debug_port)

    print("Starting Stockfish...")
    engine = chess.engine.SimpleEngine.popen_uci(args.stockfish_path)

    last_fen = None
    try:
        print("Watching the board. Press Ctrl+C to stop.\n")
        while True:
            try:
                board_el = find_chess_board_element(driver)
                flipped = board_is_flipped(driver, board_el)
                pieces = read_board_pieces(driver)
                if not pieces:
                    time.sleep(args.poll_interval)
                    continue

                turn_guess = guess_turn_from_move_list(driver)
                fen = pieces_to_fen(pieces, flipped, turn_guess)

                if fen != last_fen:
                    last_fen = fen
                    try:
                        move, score, turn = get_best_move(engine, fen, args.think_time)
                        side = "White" if turn == chess.WHITE else "Black"
                        print(f"[{side} to move] Suggested: {move}   (eval: {score})")
                    except Exception as e:
                        print(f"Could not evaluate position: {e}")

            except (NoSuchElementException, WebDriverException):
                print("Board not found on page yet — make sure a game is open.")

            time.sleep(args.poll_interval)

    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        engine.quit()


if __name__ == "__main__":
    main()
