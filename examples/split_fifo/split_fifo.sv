`default_nettype none

// Simple sync FIFO implementation using an extra bit in the read and write
// pointers to distinguish the completely full and completely empty case.
module fifo #(
    DEPTH_BITS = 2,
    WIDTH = 8
) (
    input wire clk,
    input wire rst,

    input wire             in_valid,
    input wire [WIDTH-1:0] in_data,
    output reg             in_ready,

    output reg             out_valid,
    output reg [WIDTH-1:0] out_data,
    input wire             out_ready
);

    reg [WIDTH-1:0] buffer [1<<DEPTH_BITS];

    reg [DEPTH_BITS:0] write_addr;
    reg [DEPTH_BITS:0] read_addr;

    wire in_transfer = in_valid && in_ready;
    wire out_transfer = out_valid && out_ready;

    wire [DEPTH_BITS:0] write_limit = read_addr ^ (1 << DEPTH_BITS);

    assign in_ready = write_addr != write_limit;
    assign out_valid = read_addr != write_addr;

    assign out_data = buffer[read_addr[DEPTH_BITS-1:0]];

    always @(posedge clk) begin
        if (rst) begin
            read_addr <= 0;
            write_addr <= 0;
        end else begin
            if (in_transfer) begin
                buffer[write_addr[DEPTH_BITS-1:0]] <= in_data;
                write_addr <= write_addr + 1'b1;
            end

            if (out_transfer) begin
                read_addr <= read_addr + 1'b1;
            end
        end
    end

endmodule

// Toy multiplier circuit that takes a value-dependent variable number of
// cycles.
module multiplier #(
    WIDTH = 4
) (
    input wire clk,
    input wire rst,

    input wire               in_valid,
    input wire [2*WIDTH-1:0] in_data,
    output reg               in_ready,

    output reg               out_valid,
    output reg [2*WIDTH-1:0] out_data,
    input wire               out_ready
);

    reg [2*WIDTH-1:0] acc;
    reg [2*WIDTH-1:0] a;
    reg [  WIDTH-1:0] b;

    wire [WIDTH-1:0] in_a;
    wire [WIDTH-1:0] in_b;

    assign {in_b, in_a} = in_data;

    wire done = b == 0;

    assign out_data = acc;
    assign out_valid = (!in_ready) && done;

    always @(posedge clk) begin
        if (b[0]) begin
            acc <= acc + a;
        end

        b <= b >> 1;
        a <= a << 1;

        if (in_ready && in_valid) begin
            {a, b} <= in_a < in_b ? {in_b, in_a} : {in_a, in_b};
            acc <= 0;
            in_ready <= 0;
        end

        if (out_ready && out_valid) begin
            in_ready <= 1;
        end

        if (rst) begin
            in_ready <= 1;
            acc <= 0;
        end
    end
endmodule

// Composition of a multiplier with an input and output FIFO
module lane #(
    WIDTH = 4,
    DEPTH_BITS = 2
) (
    input wire clk,
    input wire rst,

    input wire               in_valid,
    input wire [2*WIDTH-1:0] in_data,
    output reg               in_ready,

    output reg               out_valid,
    output reg [2*WIDTH-1:0] out_data,
    input wire               out_ready
);

    wire               mult_in_valid;
    wire [2*WIDTH-1:0] mult_in_data;
    wire               mult_in_ready;

    wire               mult_out_valid;
    wire [2*WIDTH-1:0] mult_out_data;
    wire               mult_out_ready;

    fifo #(.WIDTH(2*WIDTH), .DEPTH_BITS(DEPTH_BITS)) in_fifo (
        .clk(clk),
        .rst(rst),

        .in_valid(in_valid),
        .in_data(in_data),
        .in_ready(in_ready),

        .out_valid(mult_in_valid),
        .out_data(mult_in_data),
        .out_ready(mult_in_ready)
    );

    fifo #(.WIDTH(2*WIDTH), .DEPTH_BITS(DEPTH_BITS)) out_fifo (
        .clk(clk),
        .rst(rst),
        .out_valid(out_valid),
        .out_data(out_data),
        .out_ready(out_ready),

        .in_valid(mult_out_valid),
        .in_data(mult_out_data),
        .in_ready(mult_out_ready)
    );

    multiplier #(.WIDTH(WIDTH)) multiplier (
        .clk(clk),
        .rst(rst),

        .in_valid(mult_in_valid),
        .in_data(mult_in_data),
        .in_ready(mult_in_ready),
        .out_valid(mult_out_valid),
        .out_data(mult_out_data),
        .out_ready(mult_out_ready)
    );
endmodule


// Contains two lanes in parallel together with a common input and output FIFO
// each containing pairs of multiplication requests. Requests can only enter
// the per-lane FIFOs when both per-lane FIFOs are ready and responses can only
// leave the per-lane FIFOs when they both have valid data.
module top #(
    WIDTH = 4
) (
    input wire clk,
    input wire rst,

    input wire               in_valid,
    input wire [WIDTH*4-1:0] in_data,
    output reg               in_ready,


    output reg               out_valid,
    output reg [WIDTH*4-1:0] out_data,
    input wire               out_ready
);

    wire fifo_out_valid;
    wire fifo_in_ready;

    wire               lane_in_valid;
    wire [WIDTH*4-1:0] lane_in_data;
    wire [        1:0] lane_in_ready;
    wire [        1:0] lane_out_valid;
    wire [WIDTH*4-1:0] lane_out_data;
    wire               lane_out_ready;

    assign lane_in_valid = fifo_out_valid & (&lane_in_ready);
    assign lane_out_ready = fifo_in_ready & (&lane_out_valid);

    fifo #(.WIDTH(WIDTH*4)) input_fifo (
        .clk(clk), .rst(rst),
        .in_valid(in_valid),
        .in_data(in_data),
        .in_ready(in_ready),
        .out_valid(fifo_out_valid),
        .out_data(lane_in_data),
        .out_ready(&lane_in_ready)
    );

    lane lane [1:0] (
        .clk(clk), .rst(rst),
        .in_valid(lane_in_valid),
        .in_data(lane_in_data),
        .in_ready(lane_in_ready),
        .out_valid(lane_out_valid),
        .out_data(lane_out_data),
        .out_ready(lane_out_ready)
    );

    fifo #(.WIDTH(WIDTH*4)) output_fifo (
        .clk(clk), .rst(rst),
        .in_valid(&lane_out_valid),
        .in_data(lane_out_data),
        .in_ready(fifo_in_ready),
        .out_valid(out_valid),
        .out_data(out_data),
        .out_ready(out_ready)
    );

    /// This design has representable states in which it would be stuck. E.g.
    /// when both FIFOs of `lane[0]` are full and the multiplier of that lane
    /// is active while `lane[1]` is completely empty.
    ///
    /// As part of ensuring that these states are not reachable, we would like
    /// to prove the following property which says that whenever an input
    /// request enters, we get a valid output response within the next 9
    /// cycles. (Note that we don't require the output to be the one
    /// corresponding for the given input, as that's not required to ensure
    /// progress.)
    property progress;
        @(posedge clk) disable iff (rst)
        (in_valid && in_ready) |=> ##[0:8] out_valid;
    endproperty
endmodule
